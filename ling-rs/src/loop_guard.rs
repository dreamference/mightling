//! The loop guard: what stops a model that repeats itself (docs/dev/loop-guard.md).
//!
//! On 2026-10-20 a phone question stalled for 15 minutes: the model looped inside a tool call's
//! argument, which SGLang's tool parser buffers whole, so nothing reached `ling` until the 900 s
//! idle timeout. Two layers answer that, both through Codex's own extension API, with no Codex
//! patch (installed beside the air-gap section, [`install`]):
//!
//! - **A length cap per response**, the only bound SGLang's `/v1/responses` honours. The launcher
//!   cannot write the request body, so it names the cap in the request's `client_metadata`
//!   (`ling_max_output_tokens`) and the model gate, which reads the body anyway, sets
//!   `max_output_tokens` when the body has none (`dreamference/vllm_server/model_gate_guard.py`).
//!   The same path carries a sampling arm (`ling_sampling`). The cap ends a buffered loop in a
//!   bounded time; a loop that streams is ended sooner by the detector.
//! - **A detector on the streamed text** (the leaf crate `ling-rs/loop-guard`): reasoning and answer
//!   text go through three checks (periodic suffix, compression ratio, character and token runs).
//!
//! Either way the response is cut where the loop began and the turn goes on: the text before the
//! loop is kept, a short developer message tells the model to stop and answer, and the response
//! completes with `end_turn: false`, so Codex sends the next request in the same turn. If the very
//! next response of that thread is stopped too, the turn ends with a plain message to the user.
//! A response that completes normally resets this, so a long agentic turn can be rescued more than
//! once, but never twice in a row. A response the server ends as incomplete (the cap, or a
//! server-side repetition stop) is handled the same way: Codex would otherwise retry it up to five
//! times, rerunning the loop. Tool calls are held until the response completes, so a call cut off by
//! the cap is never run.
//!
//! **Surfaces.** What a request gets depends on where it comes from: the TUI, `ling exec` (which
//! is also how SWE-bench and Night Shift run the agent), and, inside `ling app-server`, Work
//! threads, Ask threads (cwd under `$CODEX_HOME/ask/`) and phone threads (`$CODEX_HOME/ask/chat-*`,
//! the scratch folders `ling web` makes for the bridges' `chat` prompt). `exec` gets nothing by
//! default, so SWE-bench and Night Shift send byte-identical requests; a benchmark arm switches
//! each layer on through [`GUARD_ENV`], [`BUDGET_ENV`] and [`SAMPLING_ENV`]. Every detection is a
//! line in `$CODEX_HOME/loop-guard/events.jsonl`; `ling loop-guard` shows the settings and counts.

use std::collections::BTreeMap;
use std::collections::HashMap;
use std::collections::HashSet;
use std::collections::VecDeque;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::OnceLock;

use codex_extension_api::ContextContributor;
use codex_extension_api::ExtensionFuture;
use codex_extension_api::ExtensionRegistryBuilder;
use codex_extension_api::ModelRequestContributor;
use codex_extension_api::ModelRequestInput;
use codex_extension_api::ModelRequestKind;
use codex_extension_api::ModelResponseError;
use codex_extension_api::ModelResponseInterceptor;
use codex_extension_api::ModelResponseStream;
use codex_extension_api::ResponseEvent;
use codex_extension_api::ResponseItem;
use codex_extension_api::WorldStateContributionInput;
use codex_extension_api::WorldStateSectionContribution;
use futures::StreamExt;
use ling_loop_guard::Detector;
use ling_loop_guard::Kind;
use ling_loop_guard::Thresholds;
use serde_json::Value;
use serde_json::json;

/// `on` or `off`: the detector for every surface of this process, over the defaults and the file.
pub const GUARD_ENV: &str = "DREAMFERENCE_MIGHTLING_LOOP_GUARD";
/// A number of tokens: the cap on each response for every surface of this process; `0` is none.
pub const BUDGET_ENV: &str = "DREAMFERENCE_MIGHTLING_OUTPUT_BUDGET";
/// `nonthinking` asks the gate for the model card's non-thinking sampling; `default` or unset sends
/// nothing, and the server's defaults apply.
pub const SAMPLING_ENV: &str = "DREAMFERENCE_MIGHTLING_SAMPLING";
/// The Dreamference TOML file's table for per-surface settings and thresholds.
pub const TABLE: &str = "loop_guard";

/// The `client_metadata` keys the model gate reads (`model_gate_guard.py`).
pub const SURFACE_KEY: &str = "ling_surface";
pub const BUDGET_KEY: &str = "ling_max_output_tokens";
pub const SAMPLING_KEY: &str = "ling_sampling";

/// The one sampling arm there is: Qwen3.8's card's values for non-thinking mode, which is how
/// every `ling` surface runs the model (reasoning effort `none`).
pub const NONTHINKING: &str = "nonthinking";

/// The prefix of the error Codex makes of a `response.incomplete` event (codex-api's SSE parser).
const INCOMPLETE_PREFIX: &str = "Incomplete response returned";
/// The largest `max_pattern_size` a file may set. The detector keeps three rings of this many
/// entries for every watched response, and no reply repeats a pattern of more pieces than this.
const MAX_PATTERN_SIZE: usize = 65_536;

/// The developer message after a loop: the turn goes on with it.
pub const LOOP_NUDGE: &str = "You were repeating yourself, so your last output was stopped. Do not repeat it. \
Stop thinking and answer now with what you have; if you need a tool, call it once.";
/// The developer message after the length cap: the turn goes on with it.
pub const LENGTH_NUDGE: &str = "Your last output reached its length limit and was cut off, most likely because it \
was repeating itself. Do not repeat it. Answer now, briefly, with what you have; if you need a tool, call it \
once with a short input.";
/// What the user reads when the response after a nudge is stopped too.
pub const ENDED_MESSAGE: &str = "I stopped this answer because I kept repeating myself, even after being told to \
stop. Please ask again, perhaps more narrowly.";

/// Where a request comes from.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Surface {
    Tui,
    Exec,
    Work,
    Ask,
    Phone,
}

impl Surface {
    pub const ALL: [Surface; 5] = [Surface::Tui, Surface::Exec, Surface::Work, Surface::Ask, Surface::Phone];

    pub fn name(self) -> &'static str {
        match self {
            Surface::Tui => "tui",
            Surface::Exec => "exec",
            Surface::Work => "work",
            Surface::Ask => "ask",
            Surface::Phone => "phone",
        }
    }

    /// The per-response cap when nothing else says: Ask's longest response in the saved sessions
    /// was 361 tokens, SWE-bench's 21,196 (a compaction summary).
    fn default_budget(self) -> Option<u32> {
        match self {
            Surface::Phone => Some(4_096),
            Surface::Ask => Some(8_192),
            Surface::Tui | Surface::Work => Some(32_768),
            Surface::Exec => None,
        }
    }

    fn default_guard(self) -> bool {
        self != Surface::Exec
    }
}

/// The kind of process, set once by the launcher before Codex starts ([`configure`]).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Process {
    Tui,
    Exec,
    AppServer,
}

/// What one surface gets.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub guard: bool,
    pub budget: Option<u32>,
    pub sampling: Option<String>,
    pub thresholds: Thresholds,
}

impl Settings {
    /// Whether requests from the surface are left exactly as Codex makes them.
    pub fn untouched(&self) -> bool {
        !self.guard && self.budget.is_none() && self.sampling.is_none()
    }
}

/// The settings of `surface`: defaults, then the TOML table (`<surface>_guard`,
/// `<surface>_max_output_tokens`, `sampling`, the thresholds by name), then the variables.
pub fn settings_for(surface: Surface, table: Option<&toml::Table>, env: &dyn Fn(&str) -> Option<String>) -> Settings {
    let mut s = Settings {
        guard: surface.default_guard(),
        budget: surface.default_budget(),
        sampling: None,
        thresholds: Thresholds::default(),
    };
    if let Some(table) = table {
        let name = surface.name();
        if let Some(on) = table.get(&format!("{name}_guard")).and_then(toml::Value::as_bool) {
            s.guard = on;
        }
        if let Some(n) = table.get(&format!("{name}_max_output_tokens")).and_then(toml::Value::as_integer) {
            s.budget = u32::try_from(n).ok().filter(|n| *n > 0);
        }
        if let Some(arm) = table.get("sampling").and_then(toml::Value::as_str) {
            s.sampling = sampling_arm(arm);
        }
        let t = &mut s.thresholds;
        let size = |key: &str, into: &mut usize| {
            if let Some(n) = table.get(key).and_then(toml::Value::as_integer).and_then(|n| usize::try_from(n).ok()) {
                *into = n;
            }
        };
        size("max_pattern_size", &mut t.max_pattern_size);
        t.max_pattern_size = t.max_pattern_size.min(MAX_PATTERN_SIZE);
        size("min_pattern_size", &mut t.min_pattern_size);
        size("min_count", &mut t.min_count);
        size("max_run", &mut t.max_run);
        size("max_char_run", &mut t.max_char_run);
        size("compression_window", &mut t.compression_window);
        if let Some(r) = table.get("min_compression_ratio").and_then(toml::Value::as_float) {
            t.min_compression_ratio = r;
        }
    }
    if let Some(v) = env(GUARD_ENV).map(|v| v.trim().to_ascii_lowercase()) {
        match v.as_str() {
            "on" | "1" | "true" | "yes" => s.guard = true,
            "off" | "0" | "false" | "no" => s.guard = false,
            _ => {}
        }
    }
    if let Some(n) = env(BUDGET_ENV).and_then(|v| v.trim().parse::<u32>().ok()) {
        s.budget = (n > 0).then_some(n);
    }
    if let Some(v) = env(SAMPLING_ENV) {
        s.sampling = sampling_arm(&v);
    }
    s
}

fn sampling_arm(value: &str) -> Option<String> {
    (value.trim().eq_ignore_ascii_case(NONTHINKING)).then(|| NONTHINKING.to_string())
}

/// The surface of a thread of `ling app-server`, from its working folder.
pub fn surface_of_cwd(cwd: &Path, codex_home: &Path) -> Surface {
    match cwd.strip_prefix(codex_home.join("ask")) {
        Ok(rest) => match rest.components().next() {
            Some(first) if first.as_os_str().to_string_lossy().starts_with("chat-") => Surface::Phone,
            _ => Surface::Ask,
        },
        Err(_) => Surface::Work,
    }
}

struct State {
    process: Process,
    codex_home: Option<PathBuf>,
    table: Option<toml::Table>,
    /// Thread id to surface, for `ling app-server` (filled by the World State hook).
    threads: Mutex<HashMap<String, Surface>>,
    /// Threads whose last response was stopped: the next stop ends the turn.
    nudged: Mutex<HashSet<String>>,
}

static STATE: OnceLock<State> = OnceLock::new();

/// Records the kind of process; without it the guard does nothing (`ling debug`, the tests).
pub fn configure(process: Process, codex_home: &Path) {
    let table = crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok())
        .and_then(|doc| doc.get(TABLE).and_then(toml::Value::as_table).cloned());
    let _ = STATE.set(State {
        process,
        codex_home: Some(codex_home.to_path_buf()),
        table,
        threads: Mutex::new(HashMap::new()),
        nudged: Mutex::new(HashSet::new()),
    });
}

fn env_var(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.trim().is_empty())
}

fn thread_surface(state: &State, thread_id: &str) -> Surface {
    match state.process {
        Process::Tui => Surface::Tui,
        Process::Exec => Surface::Exec,
        Process::AppServer => state
            .threads
            .lock()
            .ok()
            .and_then(|threads| threads.get(thread_id).copied())
            .unwrap_or(Surface::Work),
    }
}

/// The `client_metadata` entries a request from `surface` carries; none when it is untouched.
pub fn metadata(surface: Surface, settings: &Settings) -> Option<HashMap<String, String>> {
    if settings.untouched() {
        return None;
    }
    let mut out = HashMap::from([(SURFACE_KEY.to_string(), surface.name().to_string())]);
    if let Some(budget) = settings.budget {
        out.insert(BUDGET_KEY.to_string(), budget.to_string());
    }
    if let Some(arm) = &settings.sampling {
        out.insert(SAMPLING_KEY.to_string(), arm.clone());
    }
    Some(out)
}

#[derive(Debug)]
struct Guard;

impl ModelRequestContributor for Guard {
    fn request(&self, input: ModelRequestInput<'_>) -> Option<Box<dyn ModelResponseInterceptor>> {
        let state = STATE.get()?;
        if input.kind != ModelRequestKind::Generation {
            return None;
        }
        let surface = thread_surface(state, input.thread_id);
        let settings = settings_for(surface, state.table.as_ref(), &env_var);
        *input.client_metadata = metadata(surface, &settings);
        if !settings.guard && settings.budget.is_none() {
            return None;
        }
        Some(Box::new(Interceptor {
            thread: input.thread_id.to_string(),
            surface,
            thresholds: settings.thresholds,
            detect: settings.guard,
        }))
    }
}

/// Learns each app-server thread's surface from its working folder; adds nothing to the prompt.
struct Surfaces;

impl ContextContributor for Surfaces {
    fn contribute_world_state<'a>(
        &'a self,
        input: WorldStateContributionInput<'a>,
    ) -> ExtensionFuture<'a, Vec<WorldStateSectionContribution>> {
        Box::pin(async move {
            if let Some(state) = STATE.get()
                && state.process == Process::AppServer
                && let Some(home) = &state.codex_home
                && let Some(environment) = input.environments.first()
            {
                let surface = surface_of_cwd(&environment.cwd.to_path_buf(), home);
                if let Ok(mut threads) = state.threads.lock() {
                    threads.insert(input.thread_id.to_string(), surface);
                }
            }
            Vec::new()
        })
    }
}

/// Registers the guard. Called from `airgapped::install`, which the app server's registry and
/// `ling debug prompt-input`'s already call, so it costs no patch bytes.
pub fn install<C: Sync>(registry: &mut ExtensionRegistryBuilder<C>) {
    registry.prompt_contributor(Arc::new(Surfaces));
    registry.model_request_contributor(Arc::new(Guard));
}

struct Interceptor {
    thread: String,
    surface: Surface,
    thresholds: Thresholds,
    detect: bool,
}

impl ModelResponseInterceptor for Interceptor {
    fn intercept(self: Box<Self>, stream: ModelResponseStream) -> ModelResponseStream {
        let nudged = move |thread: &str, set: Option<bool>| -> bool {
            let Some(state) = STATE.get() else { return false };
            let Ok(mut nudged) = state.nudged.lock() else { return false };
            match set {
                Some(true) => !nudged.insert(thread.to_string()),
                Some(false) => nudged.remove(thread),
                None => nudged.contains(thread),
            }
        };
        let watch = Watch::new(self.thread, self.surface, self.thresholds, self.detect, Box::new(nudged));
        guarded(stream, watch)
    }
}

/// Asks or sets whether a thread was just nudged: `Some(true)` marks it (answering whether it was
/// already), `Some(false)` clears it, `None` asks.
pub type NudgedFn = Box<dyn Fn(&str, Option<bool>) -> bool + Send + Sync>;

/// One streamed item being watched: what the server said it is, and its text so far.
struct Watched {
    item: Value,
    detector: Detector,
}

/// The state of one guarded response.
pub struct Watch {
    thread: String,
    surface: Surface,
    thresholds: Thresholds,
    detect: bool,
    nudged: NudgedFn,
    current: Option<Watched>,
    /// Items held until the response completes: a tool call, and whatever follows it.
    held: Vec<ResponseItem>,
    holding: bool,
}

/// Why a response was stopped.
#[derive(Clone, Debug, PartialEq)]
pub enum Stop {
    Loop(Kind),
    /// The server ended it as incomplete, with this reason.
    Incomplete(String),
}

impl Stop {
    fn name(&self) -> String {
        match self {
            Stop::Loop(kind) => kind.name().to_string(),
            Stop::Incomplete(reason) => format!("incomplete:{reason}"),
        }
    }
}

impl Watch {
    pub fn new(thread: String, surface: Surface, thresholds: Thresholds, detect: bool, nudged: NudgedFn) -> Self {
        Watch { thread, surface, thresholds, detect, nudged, current: None, held: Vec::new(), holding: false }
    }

    /// Takes one upstream event; returns what goes downstream, and whether the response is over
    /// (stopped by the guard, or ended by the server).
    pub fn on_event(&mut self, event: Result<ResponseEvent, ModelResponseError>) -> (Vec<Result<ResponseEvent, ModelResponseError>>, bool) {
        let event = match event {
            Ok(event) => event,
            Err(ModelResponseError::Stream(message)) if message.starts_with(INCOMPLETE_PREFIX) => {
                let reason = message.rsplit("reason: ").next().unwrap_or("unknown").trim().to_string();
                return (self.stop(Stop::Incomplete(reason)), true);
            }
            Err(err) => {
                // Anything else is Codex's to handle, exactly as without the guard.
                let mut out: Vec<_> = self.held.drain(..).map(|item| Ok(ResponseEvent::OutputItemDone(item))).collect();
                out.push(Err(err));
                return (out, true);
            }
        };
        match event {
            ResponseEvent::OutputItemAdded(item) => {
                self.current = match &item {
                    ResponseItem::Reasoning { .. } => Some(self.watched(&item)),
                    ResponseItem::Message { role, .. } if role == "assistant" => Some(self.watched(&item)),
                    _ => None,
                };
                (vec![Ok(ResponseEvent::OutputItemAdded(item))], false)
            }
            ResponseEvent::ReasoningContentDelta { delta, content_index } => {
                let found = self.push(&delta);
                let mut out = vec![Ok(ResponseEvent::ReasoningContentDelta { delta, content_index })];
                if let Some(kind) = found {
                    out.extend(self.stop(Stop::Loop(kind)));
                    return (out, true);
                }
                (out, false)
            }
            ResponseEvent::ReasoningSummaryDelta { delta, summary_index } => {
                let found = self.push(&delta);
                let mut out = vec![Ok(ResponseEvent::ReasoningSummaryDelta { delta, summary_index })];
                if let Some(kind) = found {
                    out.extend(self.stop(Stop::Loop(kind)));
                    return (out, true);
                }
                (out, false)
            }
            ResponseEvent::OutputTextDelta(delta) => {
                let found = self.push(&delta);
                let mut out = vec![Ok(ResponseEvent::OutputTextDelta(delta))];
                if let Some(kind) = found {
                    out.extend(self.stop(Stop::Loop(kind)));
                    return (out, true);
                }
                (out, false)
            }
            ResponseEvent::OutputItemDone(item) => {
                let is_text = matches!(item, ResponseItem::Reasoning { .. } | ResponseItem::Message { .. });
                if is_text {
                    self.current = None;
                }
                if self.holding || !is_text {
                    self.holding = true;
                    self.held.push(item);
                    return (Vec::new(), false);
                }
                (vec![Ok(ResponseEvent::OutputItemDone(item))], false)
            }
            ResponseEvent::Completed { response_id, token_usage, usage_metadata, end_turn } => {
                (self.nudged)(&self.thread, Some(false));
                let mut out: Vec<_> = self.held.drain(..).map(|item| Ok(ResponseEvent::OutputItemDone(item))).collect();
                out.push(Ok(ResponseEvent::Completed { response_id, token_usage, usage_metadata, end_turn }));
                (out, true)
            }
            other => (vec![Ok(other)], false),
        }
    }

    /// The end of the upstream stream without a terminal event: whatever was held goes on.
    pub fn on_end(&mut self) -> Vec<Result<ResponseEvent, ModelResponseError>> {
        self.held.drain(..).map(|item| Ok(ResponseEvent::OutputItemDone(item))).collect()
    }

    fn watched(&self, item: &ResponseItem) -> Watched {
        Watched {
            item: serde_json::to_value(item).unwrap_or(Value::Null),
            detector: Detector::new(self.thresholds.clone()),
        }
    }

    fn push(&mut self, delta: &str) -> Option<Kind> {
        if !self.detect {
            return None;
        }
        let watched = self.current.as_mut()?;
        watched.detector.push(delta).map(|found| found.kind)
    }

    /// Ends the response: the watched item cut where its loop began, the held tool calls dropped,
    /// then a nudge and `end_turn: false`, or, right after a nudge, a plain message and the end.
    fn stop(&mut self, why: Stop) -> Vec<Result<ResponseEvent, ModelResponseError>> {
        let mut out = Vec::new();
        let mut streamed = 0;
        if let Some(watched) = self.current.take() {
            streamed = watched.detector.pieces();
            let text = watched.detector.text();
            let kept = match watched.detector.detection() {
                Some(found) => &text[..found.cut],
                None => text,
            };
            if let Some(item) = finished_item(watched.item, kept) {
                out.push(Ok(ResponseEvent::OutputItemDone(item)));
            }
        }
        // A tool call the cap cut short is never run; a whole one before a loop is lost too, since
        // the response it belongs to is being replaced.
        let dropped = self.held.len();
        self.held.clear();
        let again = (self.nudged)(&self.thread, Some(true));
        let (message, end_turn, action) = if again {
            (self.nudged)(&self.thread, Some(false));
            (assistant_message(ENDED_MESSAGE), true, "ended")
        } else {
            let nudge = if matches!(why, Stop::Loop(_)) { LOOP_NUDGE } else { LENGTH_NUDGE };
            (developer_message(nudge), false, "nudged")
        };
        if let Some(message) = message {
            out.push(Ok(ResponseEvent::OutputItemDone(message)));
        }
        out.push(Ok(ResponseEvent::Completed {
            response_id: format!("ling-loop-guard-{}", std::process::id()),
            token_usage: None,
            usage_metadata: None,
            end_turn: Some(end_turn),
        }));
        record(&json!({
            "time": chrono::Utc::now().to_rfc3339(),
            "thread": self.thread,
            "surface": self.surface.name(),
            "check": why.name(),
            "action": action,
            "streamed_pieces": streamed,
            "dropped_items": dropped,
        }));
        out
    }
}

/// The item the server opened, finished with `text` in place of what it streamed.
fn finished_item(mut item: Value, text: &str) -> Option<ResponseItem> {
    let object = item.as_object_mut()?;
    match object.get("type").and_then(Value::as_str) {
        Some("reasoning") => {
            object.insert("summary".into(), json!([]));
            object.insert("content".into(), json!([{"type": "reasoning_text", "text": text}]));
        }
        Some("message") => {
            object.insert("content".into(), json!([{"type": "output_text", "text": text}]));
        }
        _ => return None,
    }
    object.remove("status");
    serde_json::from_value(item).ok()
}

fn developer_message(text: &str) -> Option<ResponseItem> {
    serde_json::from_value(json!({"type": "message", "role": "developer", "content": [{"type": "input_text", "text": text}]})).ok()
}

fn assistant_message(text: &str) -> Option<ResponseItem> {
    serde_json::from_value(json!({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]})).ok()
}

/// Wraps `stream` so every event passes through `watch`; once the response is over, upstream is
/// dropped, which closes the connection and makes the server stop generating.
pub fn guarded(stream: ModelResponseStream, watch: Watch) -> ModelResponseStream {
    struct Run {
        upstream: Option<ModelResponseStream>,
        watch: Watch,
        queue: VecDeque<Result<ResponseEvent, ModelResponseError>>,
    }
    let run = Run { upstream: Some(stream), watch, queue: VecDeque::new() };
    futures::stream::unfold(run, |mut run| async move {
        loop {
            if let Some(event) = run.queue.pop_front() {
                return Some((event, run));
            }
            let upstream = run.upstream.as_mut()?;
            match upstream.next().await {
                Some(event) => {
                    let (out, over) = run.watch.on_event(event);
                    run.queue.extend(out);
                    if over {
                        run.upstream = None;
                    }
                }
                None => {
                    run.upstream = None;
                    let rest = run.watch.on_end();
                    run.queue.extend(rest);
                }
            }
        }
    })
    .boxed()
}

fn events_path(codex_home: &Path) -> PathBuf {
    codex_home.join("loop-guard").join("events.jsonl")
}

fn record(event: &Value) {
    let Some(home) = STATE.get().and_then(|state| state.codex_home.clone()) else { return };
    let path = events_path(&home);
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(file, "{event}");
    }
}

const USAGE: &str = "usage: ling loop-guard [status]";

/// `ling loop-guard`: each surface's settings and what the guard has stopped, by surface, check
/// and action.
pub fn run_cli(args: &[String]) -> i32 {
    if !matches!(args.iter().map(String::as_str).collect::<Vec<_>>().as_slice(), [] | ["status"]) {
        eprintln!("{USAGE}");
        return if matches!(args.first().map(String::as_str), Some("-h" | "--help" | "help")) { 0 } else { 2 };
    }
    let table = crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok())
        .and_then(|doc| doc.get(TABLE).and_then(toml::Value::as_table).cloned());
    let home = codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf());
    let events = home.map(|home| std::fs::read_to_string(events_path(&home)).unwrap_or_default()).unwrap_or_default();
    for line in status_lines(table.as_ref(), &env_var, &events) {
        println!("{line}");
    }
    0
}

/// The lines `ling loop-guard` prints.
pub fn status_lines(table: Option<&toml::Table>, env: &dyn Fn(&str) -> Option<String>, events: &str) -> Vec<String> {
    let mut lines = vec!["Loop guard, by surface (SWE-bench and Night Shift run as exec):".to_string()];
    for surface in Surface::ALL {
        let s = settings_for(surface, table, env);
        lines.push(format!(
            "  {:<6} detector {:<3}  cap {:<14}  sampling {}",
            surface.name(),
            if s.guard { "on" } else { "off" },
            s.budget.map_or("none".to_string(), |n| format!("{n} tokens")),
            s.sampling.as_deref().unwrap_or("server default"),
        ));
    }
    let mut counts: BTreeMap<(String, String, String), usize> = BTreeMap::new();
    for line in events.lines() {
        let Ok(event) = serde_json::from_str::<Value>(line) else { continue };
        let field = |key: &str| event.get(key).and_then(Value::as_str).unwrap_or("?").to_string();
        *counts.entry((field("surface"), field("check"), field("action"))).or_default() += 1;
    }
    if counts.is_empty() {
        lines.push("Nothing stopped yet.".to_string());
    } else {
        lines.push("Stopped:".to_string());
        for ((surface, check, action), n) in counts {
            lines.push(format!("  {n:>4}  {surface:<6} {check:<26} {action}"));
        }
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicBool;
    use std::sync::atomic::Ordering;

    fn no_env(_: &str) -> Option<String> {
        None
    }

    fn item(value: Value) -> ResponseItem {
        serde_json::from_value(value).expect("an item")
    }

    fn reasoning_added() -> ResponseEvent {
        ResponseEvent::OutputItemAdded(item(json!({"type": "reasoning", "id": "rs_1", "summary": [], "content": []})))
    }

    fn message_added() -> ResponseEvent {
        ResponseEvent::OutputItemAdded(item(json!({"type": "message", "id": "msg_1", "role": "assistant", "content": []})))
    }

    fn completed() -> ResponseEvent {
        ResponseEvent::Completed { response_id: "resp_1".into(), token_usage: None, usage_metadata: None, end_turn: None }
    }

    /// A nudge memory of its own, so the tests never share the process's.
    fn memory() -> (NudgedFn, Arc<AtomicBool>) {
        let flag = Arc::new(AtomicBool::new(false));
        let inner = flag.clone();
        let f: NudgedFn = Box::new(move |_, set| match set {
            Some(true) => inner.swap(true, Ordering::SeqCst),
            Some(false) => inner.swap(false, Ordering::SeqCst),
            None => inner.load(Ordering::SeqCst),
        });
        (f, flag)
    }

    fn run(events: Vec<Result<ResponseEvent, ModelResponseError>>, nudged: NudgedFn, detect: bool) -> Vec<Result<ResponseEvent, ModelResponseError>> {
        let watch = Watch::new("t".into(), Surface::Ask, Thresholds::default(), detect, nudged);
        let stream = futures::stream::iter(events).boxed();
        futures::executor::block_on(guarded(stream, watch).collect::<Vec<_>>())
    }

    fn text_of(item: &ResponseItem) -> (String, String) {
        let v = serde_json::to_value(item).unwrap_or_default();
        let kind = v.get("role").and_then(Value::as_str).or(v.get("type").and_then(Value::as_str)).unwrap_or("?").to_string();
        let text = v
            .get("content")
            .and_then(Value::as_array)
            .map(|parts| parts.iter().filter_map(|p| p.get("text").and_then(Value::as_str)).collect::<String>())
            .unwrap_or_default();
        (kind, text)
    }

    fn done_items(out: &[Result<ResponseEvent, ModelResponseError>]) -> Vec<(String, String)> {
        out.iter()
            .filter_map(|e| match e {
                Ok(ResponseEvent::OutputItemDone(item)) => Some(text_of(item)),
                _ => None,
            })
            .collect()
    }

    fn end_turn(out: &[Result<ResponseEvent, ModelResponseError>]) -> Option<Option<bool>> {
        out.iter().rev().find_map(|e| match e {
            Ok(ResponseEvent::Completed { end_turn, .. }) => Some(*end_turn),
            _ => None,
        })
    }

    fn looping_reasoning(copies: usize) -> Vec<Result<ResponseEvent, ModelResponseError>> {
        // About 50 pieces: a loop of a few sentences, as reasoning loops are.
        let block = "Wait, maybe the cache is stale. Let me check the cache again and see whether the entry was \
            written before the request, because if it was not then the second lookup would miss as well, and \
            the test would fail for a reason that has nothing to do with the change. ";
        let mut events = vec![Ok(reasoning_added())];
        events.push(Ok(ResponseEvent::ReasoningContentDelta { delta: "First, the question. ".into(), content_index: 0 }));
        for _ in 0..copies {
            for chunk in block.as_bytes().chunks(9) {
                events.push(Ok(ResponseEvent::ReasoningContentDelta { delta: String::from_utf8_lossy(chunk).into_owned(), content_index: 0 }));
            }
        }
        events.push(Ok(ResponseEvent::OutputItemDone(item(json!({"type": "reasoning", "id": "rs_1", "summary": [], "content": []})))));
        events.push(Ok(completed()));
        events
    }

    #[test]
    fn surfaces_follow_the_working_folder() {
        let home = Path::new("/home/u/.mightling");
        assert_eq!(surface_of_cwd(Path::new("/home/u/.mightling/ask/q-1a2b"), home), Surface::Ask);
        assert_eq!(surface_of_cwd(Path::new("/home/u/.mightling/ask/chat-9f"), home), Surface::Phone);
        assert_eq!(surface_of_cwd(Path::new("/home/u/.mightling/ask/chat-9f/sub"), home), Surface::Phone);
        assert_eq!(surface_of_cwd(Path::new("/home/u/src/project"), home), Surface::Work);
        assert_eq!(surface_of_cwd(Path::new("/home/u/.mightling/asking"), home), Surface::Work);
    }

    #[test]
    fn exec_and_therefore_swe_bench_and_night_shift_are_untouched_by_default() {
        let exec = settings_for(Surface::Exec, None, &no_env);
        assert!(exec.untouched());
        assert_eq!(metadata(Surface::Exec, &exec), None);
        for surface in [Surface::Tui, Surface::Work, Surface::Ask, Surface::Phone] {
            let s = settings_for(surface, None, &no_env);
            assert!(s.guard && s.budget.is_some() && s.sampling.is_none(), "{surface:?}");
        }
        assert_eq!(settings_for(Surface::Phone, None, &no_env).budget, Some(4_096));
        assert_eq!(settings_for(Surface::Ask, None, &no_env).budget, Some(8_192));
    }

    #[test]
    fn any_one_layer_on_is_enough_for_the_request_to_be_changed() {
        let off = Settings { guard: false, budget: None, sampling: None, thresholds: Thresholds::default() };
        assert!(off.untouched());
        let guard = Settings { guard: true, ..off.clone() };
        let budget = Settings { budget: Some(1_024), ..off.clone() };
        let sampling = Settings { sampling: Some(NONTHINKING.to_string()), ..off.clone() };
        for one in [guard, budget, sampling] {
            assert!(!one.untouched(), "{one:?}");
            assert!(metadata(Surface::Exec, &one).is_some(), "{one:?}");
        }
    }

    #[test]
    fn a_benchmark_arm_switches_each_layer_on_through_the_environment() {
        let env = |name: &str| match name {
            GUARD_ENV => Some("on".to_string()),
            BUDGET_ENV => Some("16384".to_string()),
            SAMPLING_ENV => Some("NonThinking".to_string()),
            _ => None,
        };
        let s = settings_for(Surface::Exec, None, &env);
        assert!(s.guard);
        assert_eq!(s.budget, Some(16_384));
        assert_eq!(s.sampling.as_deref(), Some(NONTHINKING));
        let sent = metadata(Surface::Exec, &s).expect("metadata");
        assert_eq!(sent.get(SURFACE_KEY).map(String::as_str), Some("exec"));
        assert_eq!(sent.get(BUDGET_KEY).map(String::as_str), Some("16384"));
        assert_eq!(sent.get(SAMPLING_KEY).map(String::as_str), Some("nonthinking"));
        // `0` takes the cap away, `off` the detector, an unknown arm sends nothing.
        let off = |name: &str| match name {
            GUARD_ENV => Some("off".to_string()),
            BUDGET_ENV => Some("0".to_string()),
            SAMPLING_ENV => Some("greedy".to_string()),
            _ => None,
        };
        assert!(settings_for(Surface::Ask, None, &off).untouched());
    }

    #[test]
    fn the_file_sets_surfaces_and_thresholds_and_the_environment_wins() {
        let table: toml::Table = "phone_max_output_tokens = 6000\nexec_guard = true\nask_max_output_tokens = 0\n\
            min_count = 5\nmin_compression_ratio = 0.08\nsampling = \"nonthinking\"\n"
            .parse()
            .expect("toml");
        assert_eq!(settings_for(Surface::Phone, Some(&table), &no_env).budget, Some(6_000));
        assert_eq!(settings_for(Surface::Ask, Some(&table), &no_env).budget, None);
        let exec = settings_for(Surface::Exec, Some(&table), &no_env);
        assert!(exec.guard);
        assert_eq!(exec.thresholds.min_count, 5);
        assert_eq!(exec.thresholds.min_compression_ratio, 0.08);
        assert_eq!(exec.sampling.as_deref(), Some(NONTHINKING));
        let env = |name: &str| (name == BUDGET_ENV).then(|| "2048".to_string());
        assert_eq!(settings_for(Surface::Phone, Some(&table), &env).budget, Some(2_048));
    }

    #[test]
    fn a_huge_pattern_size_in_the_file_is_capped_so_the_detector_can_be_built() {
        let table: toml::Table = format!("max_pattern_size = {}\n", i64::MAX).parse().expect("toml");
        let thresholds = settings_for(Surface::Work, Some(&table), &no_env).thresholds;
        assert_eq!(thresholds.max_pattern_size, MAX_PATTERN_SIZE);
        assert!(Detector::new(thresholds).push("x").is_none());
    }

    #[test]
    fn a_clean_response_passes_through_unchanged() {
        let (nudged, flag) = memory();
        flag.store(true, Ordering::SeqCst);
        let events = vec![
            Ok(message_added()),
            Ok(ResponseEvent::OutputTextDelta("The answer is 42.".into())),
            Ok(ResponseEvent::OutputItemDone(item(json!({"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": "The answer is 42."}]})))),
            Ok(completed()),
        ];
        let out = run(events, nudged, true);
        assert_eq!(out.len(), 4);
        assert_eq!(done_items(&out), vec![("assistant".to_string(), "The answer is 42.".to_string())]);
        assert_eq!(end_turn(&out), Some(None));
        // A response that completed clears the nudge: the next stop nudges again.
        assert!(!flag.load(Ordering::SeqCst));
    }

    #[test]
    fn a_looping_reasoning_is_cut_after_its_first_copy_and_the_turn_goes_on_with_a_nudge() {
        let (nudged, flag) = memory();
        let out = run(looping_reasoning(12), nudged, true);
        let items = done_items(&out);
        assert_eq!(items.len(), 2, "{items:?}");
        assert_eq!(items[0].0, "reasoning");
        assert!(items[0].1.starts_with("First, the question. Wait, maybe the cache is stale."));
        assert_eq!(items[0].1.matches("Wait, maybe").count(), 1, "{}", items[0].1);
        assert_eq!(items[1], ("developer".to_string(), LOOP_NUDGE.to_string()));
        assert_eq!(end_turn(&out), Some(Some(false)));
        assert!(flag.load(Ordering::SeqCst));
        // Upstream was dropped at the stop: the server's own Completed never arrives.
        assert_eq!(out.iter().filter(|e| matches!(e, Ok(ResponseEvent::Completed { .. }))).count(), 1);
    }

    #[test]
    fn a_second_stop_in_a_row_ends_the_turn_with_a_plain_message() {
        let (nudged, flag) = memory();
        flag.store(true, Ordering::SeqCst);
        let out = run(looping_reasoning(12), nudged, true);
        let items = done_items(&out);
        assert_eq!(items.last(), Some(&("assistant".to_string(), ENDED_MESSAGE.to_string())));
        assert_eq!(end_turn(&out), Some(Some(true)));
        assert!(!flag.load(Ordering::SeqCst));
    }

    #[test]
    fn an_incomplete_response_is_not_retried_and_its_cut_tool_call_never_runs() {
        let (nudged, _) = memory();
        let call = item(json!({"type": "function_call", "name": "exec_command", "arguments": "{\"cmd\": \"python3 - <<EOF\\nprint(1)\\nprint(1)", "call_id": "c1"}));
        let events = vec![
            Ok(ResponseEvent::OutputItemDone(call)),
            Err(ModelResponseError::Stream("Incomplete response returned, reason: max_output_tokens".into())),
        ];
        let out = run(events, nudged, true);
        assert!(out.iter().all(|e| e.is_ok()), "the error never reaches Codex");
        assert_eq!(done_items(&out), vec![("developer".to_string(), LENGTH_NUDGE.to_string())]);
        assert_eq!(end_turn(&out), Some(Some(false)));
    }

    #[test]
    fn a_server_side_repetition_stop_is_handled_like_the_cap() {
        let (nudged, _) = memory();
        let events = vec![
            Ok(message_added()),
            Ok(ResponseEvent::OutputTextDelta("Partial answer".into())),
            Err(ModelResponseError::Stream("Incomplete response returned, reason: repetition".into())),
        ];
        let out = run(events, nudged, true);
        assert_eq!(
            done_items(&out),
            vec![("assistant".to_string(), "Partial answer".to_string()), ("developer".to_string(), LENGTH_NUDGE.to_string())]
        );
    }

    #[test]
    fn a_whole_tool_call_is_held_until_the_response_completes_then_passed_on() {
        let (nudged, _) = memory();
        let call = item(json!({"type": "function_call", "name": "exec_command", "arguments": "{\"cmd\": \"ls\"}", "call_id": "c1"}));
        let events = vec![Ok(ResponseEvent::OutputItemDone(call)), Ok(completed())];
        let out = run(events, nudged, true);
        assert_eq!(out.len(), 2);
        assert!(matches!(&out[0], Ok(ResponseEvent::OutputItemDone(ResponseItem::FunctionCall { .. }))));
        assert_eq!(end_turn(&out), Some(None));
    }

    #[test]
    fn other_errors_reach_codex_as_before() {
        let (nudged, flag) = memory();
        let events = vec![Err(ModelResponseError::Stream("stream closed before response.completed".into()))];
        let out = run(events, nudged, true);
        assert_eq!(out.len(), 1);
        assert!(matches!(&out[0], Err(ModelResponseError::Stream(m)) if m.starts_with("stream closed")));
        assert!(!flag.load(Ordering::SeqCst));
    }

    #[test]
    fn with_the_detector_off_only_the_cap_is_handled() {
        let (nudged, _) = memory();
        let out = run(looping_reasoning(12), nudged, false);
        assert_eq!(end_turn(&out), Some(None));
        assert_eq!(done_items(&out).len(), 1);
    }

    #[test]
    fn a_looping_answer_keeps_its_text_before_the_loop() {
        let (nudged, _) = memory();
        let mut events = vec![Ok(message_added()), Ok(ResponseEvent::OutputTextDelta("Здравствуйте. ".into()))];
        for _ in 0..600 {
            events.push(Ok(ResponseEvent::OutputTextDelta("!".into())));
        }
        events.push(Ok(completed()));
        let out = run(events, nudged, true);
        let items = done_items(&out);
        assert_eq!(items[0].0, "assistant");
        assert!(items[0].1.starts_with("Здравствуйте. !!!") && items[0].1.len() < 60, "{:?}", items[0]);
    }

    #[test]
    fn the_status_counts_what_was_stopped() {
        let events = "{\"surface\":\"phone\",\"check\":\"incomplete:max_output_tokens\",\"action\":\"nudged\"}\n\
            {\"surface\":\"phone\",\"check\":\"incomplete:max_output_tokens\",\"action\":\"nudged\"}\n\
            {\"surface\":\"ask\",\"check\":\"periodic\",\"action\":\"ended\"}\nnot json\n";
        let lines = status_lines(None, &no_env, events);
        assert!(lines.iter().any(|l| l.contains("phone") && l.contains("4096 tokens")));
        assert!(lines.iter().any(|l| l.trim_start().starts_with("2  phone") && l.contains("nudged")), "{lines:?}");
        assert!(lines.iter().any(|l| l.trim_start().starts_with("1  ask") && l.contains("ended")));
        assert_eq!(status_lines(None, &no_env, "").last().map(String::as_str), Some("Nothing stopped yet."));
    }

    #[test]
    fn a_setting_that_does_not_read_leaves_the_tier_below_in_place() {
        assert_eq!(sampling_arm(" NonThinking\n").as_deref(), Some(NONTHINKING));
        assert_eq!(sampling_arm("thinking"), None);
        assert_eq!(sampling_arm(""), None);

        // A budget written as a string is not a number: the surface's default stays.
        let table: toml::Table = "phone_max_output_tokens = \"6000\"\nsampling = \"nonthinking\"\n".parse().expect("toml");
        assert_eq!(settings_for(Surface::Phone, Some(&table), &no_env).budget, Some(4_096));
        // A variable that is not a number keeps the file's value; 0 lifts the cap.
        let table: toml::Table = "phone_max_output_tokens = 6000\n".parse().expect("toml");
        let env = |name: &str| (name == BUDGET_ENV).then(|| "lots".to_string());
        assert_eq!(settings_for(Surface::Phone, Some(&table), &env).budget, Some(6_000));
        let env = |name: &str| (name == BUDGET_ENV).then(|| " 0 ".to_string());
        assert_eq!(settings_for(Surface::Phone, Some(&table), &env).budget, None);
        // Any other sampling arm in the variable turns the file's arm off.
        let table: toml::Table = "sampling = \"nonthinking\"\n".parse().expect("toml");
        let env = |name: &str| (name == SAMPLING_ENV).then(|| "off".to_string());
        assert_eq!(settings_for(Surface::Work, Some(&table), &env).sampling, None);
    }

    #[test]
    fn a_cut_item_keeps_its_kind_and_loses_its_status() {
        let message = json!({"type": "message", "role": "assistant", "id": "m1", "status": "in_progress",
            "content": [{"type": "output_text", "text": "loop loop loop"}]});
        let Some(ResponseItem::Message { content, .. }) = finished_item(message, "loop") else {
            panic!("a message stays a message");
        };
        assert_eq!(serde_json::to_value(&content).unwrap_or(Value::Null), json!([{"type": "output_text", "text": "loop"}]));

        let reasoning = json!({"type": "reasoning", "id": "r1", "status": "in_progress", "summary": [{"type": "summary_text", "text": "s"}]});
        let Some(ResponseItem::Reasoning { summary, content, .. }) = finished_item(reasoning, "think") else {
            panic!("reasoning stays reasoning");
        };
        assert!(summary.is_empty());
        assert_eq!(serde_json::to_value(&content).unwrap_or(Value::Null), json!([{"type": "reasoning_text", "text": "think"}]));

        assert!(finished_item(json!({"type": "function_call", "name": "f", "arguments": "{}", "call_id": "c"}), "x").is_none());
        assert!(finished_item(json!("not an object"), "x").is_none());
    }
}
