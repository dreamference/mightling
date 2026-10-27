//! The agent table (§7.2): one row per agent, rebuilt from what each transcript gained since the
//! last check. Files are opened read-only and read from a byte offset kept in the monitor's own state;
//! a file that shrank or was replaced is read again from its start. The table keeps counts, times and
//! hashes, never message text.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::collections::VecDeque;
use std::hash::Hash;
use std::hash::Hasher;
use std::io::Read;
use std::io::Seek;
use std::path::Path;
use std::path::PathBuf;

use serde::Deserialize;
use serde::Serialize;

use crate::limits;
use crate::limits::Limit;
use crate::transcript;
use crate::transcript::Entry;
use crate::transcript::Line;
use crate::transcript::LineClass;
use crate::transcript::ToolUse;
use crate::transcript::Usage;

/// The calls kept per agent for the loop checks of C4.
pub const RECENT_CALLS: usize = 20;
/// Hours of token counts kept per agent.
const HOURS_KEPT: i64 = 48;
/// An idle-wake rewrite: a response 5 minutes or more after the previous one that wrote more than
/// half its context into the cache (§2.1).
pub const IDLE_WAKE_GAP: i64 = 300;

/// What an unanswered tool call is waiting for (C2).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum WaitKind {
    Ci,
    Night,
    Shell,
    Subagent,
    Tool,
}

impl WaitKind {
    pub fn label(self) -> &'static str {
        match self {
            WaitKind::Ci => "CI",
            WaitKind::Night => "night window",
            WaitKind::Shell => "shell",
            WaitKind::Subagent => "subagent",
            WaitKind::Tool => "tool",
        }
    }

    /// Classes one tool call by its name and command pattern.
    pub fn of(tool: &ToolUse) -> WaitKind {
        match tool.name.as_str() {
            "Agent" | "Task" => WaitKind::Subagent,
            "TaskOutput" | "BashOutput" | "Monitor" => WaitKind::Shell,
            "Bash" => {
                let command = tool.command.as_deref().unwrap_or_default().to_ascii_lowercase();
                if ["gh run watch", "gh run view", "gh pr checks"].iter().any(|p| command.contains(p)) {
                    WaitKind::Ci
                } else if ["ling night", "ling-admin night", "swe-bench"].iter().any(|p| command.contains(p)) {
                    WaitKind::Night
                } else if is_wait_command(&command) {
                    WaitKind::Shell
                } else {
                    WaitKind::Tool
                }
            }
            _ => WaitKind::Tool,
        }
    }
}

/// A shell command whose purpose is to wait: `sleep`, `until`, `timeout`, a `while` loop, `wait`.
pub fn is_wait_command(command: &str) -> bool {
    let command = command.trim_start();
    ["sleep ", "until ", "timeout ", "while ", "wait"].iter().any(|p| command.starts_with(p))
        || command.contains("; sleep ")
        || command.contains("&& sleep ")
        || command.contains("gh run watch")
        || command.contains("gh run view")
}

/// A command that runs a test suite: `pytest` (or `-m pytest`) as a word, `cargo test`, `npm test`,
/// `npm run e2e`, `ctest`. A `grep` for the word "pytest.fail" is not one.
pub fn is_test_command(command: &str) -> bool {
    let words: Vec<&str> = command.split(|c: char| c.is_whitespace() || c == ';' || c == '&' || c == '|' || c == '(').collect();
    words.iter().any(|w| *w == "pytest" || w.ends_with("/pytest") || *w == "ctest")
        || ["-m pytest", "cargo test", "npm test", "npm run e2e"].iter().any(|p| command.contains(p))
}

fn is_install_command(command: &str) -> bool {
    ["codex build", "pip install", "install.sh", "ling update", "host setup", "cargo install", "npm run package", "ln -s", "ln -sf"]
        .iter()
        .any(|p| command.contains(p))
}

/// A tool call waiting for its result.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Pending {
    pub tool: String,
    pub wait: WaitKind,
    pub since: i64,
    /// A shell command that runs a test suite, for C5(c).
    pub test: bool,
    /// A `gh run` call, whose result may report a release.
    pub gh_run: bool,
    /// A `git push`, whose result may carry the pre-push hook's refusal.
    #[serde(default)]
    pub push: bool,
}

/// One recent call, for C4: the kind and a hash of its normalised command or path, never the text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Call {
    Command { hash: u64, wait: bool },
    Read { hash: u64 },
    Edit,
    Other,
}

/// What the agent did last.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub enum Last {
    #[default]
    Nothing,
    /// A text turn with no tool call; `question` when it ends with a question mark.
    Text { question: bool },
    /// A tool call, waiting for its result.
    Tool,
    /// A tool result: the model owes the next response.
    Result,
    /// A user turn or a notification: the model owes the next response.
    User,
    /// The owner interrupted the turn.
    Interrupted,
    /// A usage limit stopped it (its key).
    Limit(String),
    /// Another API error.
    Error,
}

/// Tokens in one hour, deduplicated by message id.
#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct Tokens {
    pub usage: Usage,
    pub responses: u64,
    pub weighted: f64,
    pub idle_wakes: u64,
    pub idle_wake_weighted: f64,
}

/// One row of the agent table.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Agent {
    /// The session id, for a coordinator; `<session>/<agent id>` for a subagent.
    pub key: String,
    pub session: String,
    pub subagent: bool,
    /// The subagent's type from its `.meta.json` (`general-purpose`); `coordinator` otherwise.
    pub agent_type: String,
    /// The subagent's task description from `.meta.json`: shown in the summary on this machine,
    /// never in an alert (D13).
    pub description: String,
    pub first_ts: i64,
    pub last_ts: i64,
    pub last_response_ts: i64,
    /// Context of the last response: input + cache writes + cache reads.
    pub context: u64,
    pub peak_context: u64,
    /// Since when the context has been at or over the hand-off threshold.
    pub over_handoff_since: Option<i64>,
    pub pending: BTreeMap<String, Pending>,
    pub last: Last,
    pub error_tool: String,
    pub error_streak: u32,
    pub recent: VecDeque<Call>,
    /// Recent message ids with the tokens already counted, and the hour they went into.
    pub ids: VecDeque<(String, Usage, i64)>,
    pub hours: BTreeMap<i64, Tokens>,
    /// The working directory the transcript names, for C5(a).
    pub cwd: Option<String>,
}

/// Something that happened, kept 48 hours for the checks that look back.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum EventKind {
    Limit(Limit),
    PushRefused,
    HomeGuard,
    Release { succeeded: bool },
    TestRun,
    Install,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Event {
    pub ts: i64,
    pub agent: String,
    pub kind: EventKind,
}

/// Where the reader is in one file.
#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct FileMark {
    pub inode: u64,
    pub offset: u64,
}

/// How the lines read in one check were classed (for `doctor` and the format-drift check).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LineCounts {
    pub used: u64,
    pub skipped: u64,
    pub unknown_type: u64,
    pub unclassed: u64,
    pub unknown_types: BTreeSet<String>,
    pub versions: BTreeSet<String>,
    pub unreadable: Vec<String>,
}

/// The whole table, as the monitor's state keeps it between checks.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Table {
    pub files: BTreeMap<String, FileMark>,
    pub agents: BTreeMap<String, Agent>,
    pub events: Vec<Event>,
    #[serde(skip)]
    pub counts: LineCounts,
    /// C1's hand-off threshold, set from the config before lines are added: the clock of an agent
    /// over it starts at the response that crossed it.
    #[serde(skip, default = "default_handoff")]
    pub handoff_context: u64,
}

fn default_handoff() -> u64 {
    300_000
}

impl Default for Table {
    fn default() -> Table {
        Table {
            files: BTreeMap::new(),
            agents: BTreeMap::new(),
            events: Vec::new(),
            counts: LineCounts::default(),
            handoff_context: default_handoff(),
        }
    }
}

/// A transcript file and the agent it belongs to.
#[derive(Debug, Clone, PartialEq)]
pub struct Source {
    pub path: PathBuf,
    pub key: String,
    pub session: String,
    pub subagent: bool,
}

/// Every session and subagent transcript under Claude Code's projects folder.
pub fn sources(projects_dir: &Path) -> Vec<Source> {
    let mut found = Vec::new();
    let Ok(projects) = std::fs::read_dir(projects_dir) else { return found };
    for project in projects.flatten() {
        let Ok(entries) = std::fs::read_dir(project.path()) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if let Some(session) = name.strip_suffix(".jsonl") {
                found.push(Source { path, key: session.to_string(), session: session.to_string(), subagent: false });
            } else if path.is_dir() {
                let Ok(agents) = std::fs::read_dir(path.join("subagents")) else { continue };
                for agent in agents.flatten() {
                    let file = agent.file_name().to_string_lossy().into_owned();
                    if let Some(id) = file.strip_prefix("agent-").and_then(|f| f.strip_suffix(".jsonl")) {
                        found.push(Source {
                            path: agent.path(),
                            key: format!("{name}/{id}"),
                            session: name.clone(),
                            subagent: true,
                        });
                    }
                }
            }
        }
    }
    found.sort_by(|a, b| a.path.cmp(&b.path));
    found
}

fn meta_of(transcript: &Path) -> (String, String) {
    let meta = transcript.with_extension("meta.json");
    let value = std::fs::read_to_string(meta).ok().and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok());
    let field = |name: &str| {
        value.as_ref().and_then(|v| v.get(name)).and_then(serde_json::Value::as_str).unwrap_or_default().to_string()
    };
    let agent_type = field("agentType");
    (if agent_type.is_empty() { "subagent".to_string() } else { agent_type }, field("description"))
}

#[cfg(unix)]
fn inode_of(meta: &std::fs::Metadata) -> u64 {
    use std::os::unix::fs::MetadataExt;
    meta.ino()
}

#[cfg(not(unix))]
fn inode_of(_meta: &std::fs::Metadata) -> u64 {
    0
}

fn hash_of(text: &str) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    text.hash(&mut hasher);
    hasher.finish()
}

/// A command with what changes between repeats masked: digit runs, long hex strings, temporary paths.
pub fn normalise(command: &str) -> String {
    let mut out = String::with_capacity(command.len());
    for word in command.split_whitespace() {
        if !out.is_empty() {
            out.push(' ');
        }
        if word.starts_with("/tmp/") || word.starts_with("\"/tmp/") {
            out.push_str("/tmp/*");
            continue;
        }
        let hexish = word.len() >= 7 && word.chars().all(|c| c.is_ascii_hexdigit()) && word.chars().any(|c| c.is_ascii_digit());
        if hexish {
            out.push('#');
            continue;
        }
        let mut in_digits = false;
        for c in word.chars() {
            if c.is_ascii_digit() {
                if !in_digits {
                    out.push('#');
                }
                in_digits = true;
            } else {
                in_digits = false;
                out.push(c);
            }
        }
    }
    out
}

fn call_of(tool: &ToolUse) -> Call {
    match tool.name.as_str() {
        "Bash" => {
            let command = tool.command.as_deref().unwrap_or_default();
            Call::Command { hash: hash_of(&normalise(command)), wait: is_wait_command(&command.to_ascii_lowercase()) }
        }
        "Read" => Call::Read { hash: hash_of(tool.file_path.as_deref().unwrap_or_default()) },
        "Edit" | "Write" | "MultiEdit" | "NotebookEdit" => Call::Edit,
        _ => Call::Other,
    }
}

impl Agent {
    fn new(source: &Source, ts: i64) -> Agent {
        let (agent_type, description) =
            if source.subagent { meta_of(&source.path) } else { ("coordinator".to_string(), String::new()) };
        Agent {
            key: source.key.clone(),
            session: source.session.clone(),
            subagent: source.subagent,
            agent_type,
            description,
            first_ts: ts,
            last_ts: ts,
            ..Agent::default()
        }
    }

    fn hour_mut(&mut self, hour: i64) -> &mut Tokens {
        self.hours.entry(hour).or_default()
    }

    /// Tokens in the hours that start at or after `since`.
    pub fn tokens_since(&self, since: i64) -> Tokens {
        let mut total = Tokens::default();
        for (_, t) in self.hours.range(since - since.rem_euclid(3600)..) {
            total.usage.input += t.usage.input;
            total.usage.cache_write += t.usage.cache_write;
            total.usage.cache_read += t.usage.cache_read;
            total.usage.output += t.usage.output;
            total.responses += t.responses;
            total.weighted += t.weighted;
            total.idle_wakes += t.idle_wakes;
            total.idle_wake_weighted += t.idle_wake_weighted;
        }
        total
    }
}

impl Table {
    /// Reads what every transcript gained since the last check and returns its lines with their
    /// agents, in file order. On a first sight, a file last written before `skip_before` is marked
    /// read to its end without being parsed (an agent idle for a day has nothing to report).
    pub fn read_new(&mut self, sources: &[Source], skip_before: Option<i64>) -> Vec<(Source, Line)> {
        let mut lines = Vec::new();
        for source in sources {
            let name = source.path.to_string_lossy().into_owned();
            let Ok(meta) = std::fs::metadata(&source.path) else {
                self.counts.unreadable.push(name);
                continue;
            };
            let inode = inode_of(&meta);
            let len = meta.len();
            let mark = self.files.get(&name).copied();
            let mut offset = match mark {
                Some(mark) if mark.inode == inode && mark.offset <= len => mark.offset,
                Some(_) => {
                    // Shrunk or replaced: read again from the start, as a new agent.
                    self.agents.remove(&source.key);
                    0
                }
                None => {
                    let modified = meta.modified().ok().and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok());
                    match (skip_before, modified) {
                        (Some(before), Some(modified)) if (modified.as_secs() as i64) < before => len,
                        _ => 0,
                    }
                }
            };
            if offset < len {
                match read_complete_lines(&source.path, offset, len) {
                    Ok((text, consumed)) => {
                        offset += consumed;
                        for raw in text.lines() {
                            if raw.trim().is_empty() {
                                continue;
                            }
                            let line = transcript::parse_line(raw);
                            match line.class {
                                LineClass::Used => self.counts.used += 1,
                                LineClass::Skipped => self.counts.skipped += 1,
                                LineClass::UnknownType => {
                                    self.counts.unknown_type += 1;
                                    if let Some(kind) = &line.kind {
                                        self.counts.unknown_types.insert(kind.clone());
                                    }
                                }
                                LineClass::Unclassed => self.counts.unclassed += 1,
                            }
                            if let Some(version) = &line.version {
                                self.counts.versions.insert(version.clone());
                            }
                            if line.class == LineClass::Used && line.ts.is_some() {
                                lines.push((source.clone(), line));
                            }
                        }
                    }
                    Err(_) => self.counts.unreadable.push(name.clone()),
                }
            }
            self.files.insert(name, FileMark { inode, offset });
        }
        lines
    }

    /// Adds one line to its agent's row.
    pub fn ingest(&mut self, source: &Source, line: &Line, zone: &limits::Zone) {
        let Some(ts) = line.ts else { return };
        let agent = self.agents.entry(source.key.clone()).or_insert_with(|| Agent::new(source, ts));
        agent.last_ts = agent.last_ts.max(ts);
        if line.cwd.is_some() {
            agent.cwd = line.cwd.clone();
        }
        for entry in &line.entries {
            match entry {
                Entry::Response { id, usage, tools, text } => {
                    let hour = ts - ts.rem_euclid(3600);
                    let known = agent.ids.iter().position(|(seen, _, _)| seen == id);
                    if let Some(usage) = usage {
                        match known {
                            Some(at) => {
                                // A later line of the same response: its output grows, and a response
                                // that made several model calls (around a server tool) sums them all
                                // on its last line. Only what grew is added.
                                let (_, counted, counted_hour) = agent.ids[at].clone();
                                let extra = Usage {
                                    input: usage.input.saturating_sub(counted.input),
                                    cache_write: usage.cache_write.saturating_sub(counted.cache_write),
                                    cache_read: usage.cache_read.saturating_sub(counted.cache_read),
                                    output: usage.output.saturating_sub(counted.output),
                                    last_call_context: None,
                                };
                                agent.ids[at].1 = Usage {
                                    input: counted.input.max(usage.input),
                                    cache_write: counted.cache_write.max(usage.cache_write),
                                    cache_read: counted.cache_read.max(usage.cache_read),
                                    output: counted.output.max(usage.output),
                                    last_call_context: None,
                                };
                                if usage.last_call_context.is_some() {
                                    agent.context = usage.context();
                                    agent.peak_context = agent.peak_context.max(agent.context);
                                }
                                let tokens = agent.hour_mut(counted_hour);
                                tokens.usage.input += extra.input;
                                tokens.usage.cache_write += extra.cache_write;
                                tokens.usage.cache_read += extra.cache_read;
                                tokens.usage.output += extra.output;
                                tokens.weighted += extra.weighted();
                            }
                            None => {
                                let context = usage.context();
                                let idle_wake = agent.last_response_ts > 0
                                    && ts - agent.last_response_ts >= IDLE_WAKE_GAP
                                    && usage.cache_write * 2 > context;
                                agent.context = context;
                                agent.peak_context = agent.peak_context.max(context);
                                if context < self.handoff_context {
                                    agent.over_handoff_since = None;
                                } else if agent.over_handoff_since.is_none() {
                                    agent.over_handoff_since = Some(ts);
                                }
                                agent.last_response_ts = ts;
                                let tokens = agent.hour_mut(hour);
                                tokens.usage.input += usage.input;
                                tokens.usage.cache_write += usage.cache_write;
                                tokens.usage.cache_read += usage.cache_read;
                                tokens.usage.output += usage.output;
                                tokens.responses += 1;
                                tokens.weighted += usage.weighted();
                                if idle_wake {
                                    tokens.idle_wakes += 1;
                                    tokens.idle_wake_weighted += 1.25 * usage.cache_write as f64;
                                }
                                agent.ids.push_back((id.clone(), *usage, hour));
                                if agent.ids.len() > 64 {
                                    agent.ids.pop_front();
                                }
                            }
                        }
                    } else if known.is_none() {
                        agent.ids.push_back((id.clone(), Usage::default(), hour));
                        if agent.ids.len() > 64 {
                            agent.ids.pop_front();
                        }
                    }
                    if let Last::Limit(_) | Last::Error = agent.last {
                        agent.last = Last::Nothing;
                    }
                    for tool in tools {
                        let command = tool.command.as_deref().unwrap_or_default().to_ascii_lowercase();
                        let test = tool.name == "Bash" && is_test_command(&command);
                        if tool.name == "Bash" && is_install_command(&command) {
                            self.events.push(Event { ts, agent: source.key.clone(), kind: EventKind::Install });
                        }
                        if test {
                            self.events.push(Event { ts, agent: source.key.clone(), kind: EventKind::TestRun });
                        }
                        agent.pending.insert(
                            tool.id.clone(),
                            Pending {
                                tool: tool.name.clone(),
                                wait: if tool.background { WaitKind::Tool } else { WaitKind::of(tool) },
                                since: ts,
                                test,
                                gh_run: command.contains("gh run"),
                                push: command.contains("git push") || command.contains("git -c") && command.contains(" push"),
                            },
                        );
                        agent.recent.push_back(call_of(tool));
                        if agent.recent.len() > RECENT_CALLS {
                            agent.recent.pop_front();
                        }
                        agent.last = Last::Tool;
                    }
                    if tools.is_empty()
                        && let Some(text) = text
                    {
                        agent.last = Last::Text { question: text.trim_end().ends_with('?') };
                    }
                }
                Entry::ApiError { error, text } => {
                    if error == "rate_limit" {
                        let limit = limits::parse(text, ts, zone);
                        agent.last = Last::Limit(limit.key());
                        self.events.push(Event { ts, agent: source.key.clone(), kind: EventKind::Limit(limit) });
                    } else {
                        agent.last = Last::Error;
                    }
                }
                Entry::ToolResult { tool_use_id, is_error, flags } => {
                    let pending = agent.pending.remove(tool_use_id);
                    let tool = pending.as_ref().map(|p| p.tool.clone()).unwrap_or_default();
                    if *is_error {
                        if agent.error_streak > 0 && agent.error_tool == tool {
                            agent.error_streak += 1;
                        } else {
                            agent.error_streak = 1;
                            agent.error_tool = tool;
                        }
                    } else {
                        agent.error_streak = 0;
                    }
                    // The hook's refusal line in the result of a push: reading the hook's source
                    // or a log that quotes it is not a refusal.
                    if flags.push_refused && pending.as_ref().is_some_and(|p| p.push) {
                        self.events.push(Event { ts, agent: source.key.clone(), kind: EventKind::PushRefused });
                    }
                    if flags.home_guard && pending.as_ref().is_some_and(|p| p.test) {
                        self.events.push(Event { ts, agent: source.key.clone(), kind: EventKind::HomeGuard });
                    }
                    if let (Some(succeeded), true, true) =
                        (flags.run_succeeded, flags.mentions_release, pending.as_ref().is_some_and(|p| p.gh_run))
                    {
                        self.events.push(Event { ts, agent: source.key.clone(), kind: EventKind::Release { succeeded } });
                    }
                    agent.last = Last::Result;
                }
                Entry::UserText { interrupted } => {
                    if *interrupted {
                        agent.pending.clear();
                        agent.last = Last::Interrupted;
                    } else {
                        agent.last = Last::User;
                    }
                }
            }
        }
    }

    /// Forgets what is too old to matter: hours past two days, agents quiet for a day, old events.
    pub fn settle(&mut self, now: i64) {
        for agent in self.agents.values_mut() {
            let oldest = now - HOURS_KEPT * 3600;
            agent.hours.retain(|hour, _| *hour >= oldest);
        }
        self.agents.retain(|_, agent| agent.last_ts >= now - 24 * 3600);
        self.events.retain(|event| event.ts >= now - 48 * 3600);
    }
}

/// Reads `[offset, len)` and returns the text up to its last newline and the bytes consumed. A
/// line still being written is left for the next check.
fn read_complete_lines(path: &Path, offset: u64, len: u64) -> std::io::Result<(String, u64)> {
    let mut file = std::fs::File::open(path)?;
    file.seek(std::io::SeekFrom::Start(offset))?;
    let mut bytes = Vec::with_capacity((len - offset) as usize);
    file.take(len - offset).read_to_end(&mut bytes)?;
    let Some(end) = bytes.iter().rposition(|b| *b == b'\n') else { return Ok((String::new(), 0)) };
    bytes.truncate(end + 1);
    Ok((String::from_utf8_lossy(&bytes).into_owned(), (end + 1) as u64))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(raw: &str) -> Line {
        transcript::parse_line(raw)
    }

    fn source() -> Source {
        Source { path: PathBuf::from("/nonexistent/s.jsonl"), key: "s".into(), session: "s".into(), subagent: false }
    }

    fn response(ts: &str, id: &str, write: u64, read: u64, out: u64, tool: Option<&str>) -> String {
        let content = match tool {
            Some(command) => format!(
                r#"[{{"type":"tool_use","id":"t-{id}","name":"Bash","input":{{"command":{}}}}}]"#,
                serde_json::to_string(command).unwrap()
            ),
            None => r#"[{"type":"text","text":"Done. Shall I merge?"}]"#.to_string(),
        };
        format!(
            r#"{{"type":"assistant","timestamp":"{ts}","message":{{"id":"{id}","content":{content},"usage":{{"input_tokens":1,"cache_creation_input_tokens":{write},"cache_read_input_tokens":{read},"output_tokens":{out}}}}}}}"#
        )
    }

    #[test]
    fn responses_are_counted_once_per_id_with_their_final_output() {
        let mut table = Table::default();
        let zone = limits::Zone::fixed("Europe/Lisbon", 0);
        let s = source();
        table.ingest(&s, &line(&response("2026-10-26T10:00:00Z", "m1", 1000, 9000, 10, None)), &zone);
        table.ingest(&s, &line(&response("2026-10-26T10:00:01Z", "m1", 1000, 9000, 300, Some("ls"))), &zone);
        table.ingest(&s, &line(&response("2026-10-26T10:10:00Z", "m2", 8000, 3000, 20, Some("ls"))), &zone);
        let agent = &table.agents["s"];
        let tokens = agent.tokens_since(0);
        assert_eq!(tokens.responses, 2);
        assert_eq!(tokens.usage.output, 320);
        assert_eq!(tokens.usage.cache_read, 12_000);
        assert_eq!(agent.context, 11_001);
        // m2 came 10 minutes later and wrote most of its context: an idle-wake rewrite.
        assert_eq!(tokens.idle_wakes, 1);
        assert_eq!(agent.pending.len(), 2);
        // A response around a server tool: its last line sums two model calls; the context is the last call's.
        let two_calls = r#"{"type":"assistant","timestamp":"2026-10-26T10:11:00Z","message":{"id":"m2","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":2,"cache_creation_input_tokens":9000,"cache_read_input_tokens":6000,"output_tokens":90,"iterations":[{"type":"message","input_tokens":1,"cache_creation_input_tokens":8000,"cache_read_input_tokens":3000},{"type":"advisor_message","input_tokens":11000},{"type":"message","input_tokens":1,"cache_creation_input_tokens":1000,"cache_read_input_tokens":3000}]}}}"#;
        table.ingest(&s, &line(two_calls), &zone);
        let agent = &table.agents["s"];
        assert_eq!(agent.context, 4001);
        let tokens = agent.tokens_since(0);
        assert_eq!((tokens.responses, tokens.usage.cache_read, tokens.usage.cache_write, tokens.usage.output), (2, 15_000, 10_000, 390));
    }

    #[test]
    fn a_refusal_counts_only_in_the_result_of_a_push() {
        let mut table = Table::default();
        let zone = limits::Zone::fixed("Europe/Lisbon", 0);
        let s = source();
        let refusal = "pre-push: tests/test_anonymity.py fails on refs/heads/x (abc); push refused. Scrub with placeholders first.";
        for (i, command) in ["cat .git/hooks/pre-push", "git push origin x"].iter().enumerate() {
            table.ingest(&s, &line(&response("2026-10-26T10:00:00Z", &format!("p{i}"), 1, 1, 1, Some(command))), &zone);
            let result = format!(
                r#"{{"type":"user","timestamp":"2026-10-26T10:00:05Z","message":{{"content":[{{"type":"tool_result","tool_use_id":"t-p{i}","is_error":true,"content":{}}}]}}}}"#,
                serde_json::to_string(refusal).unwrap()
            );
            table.ingest(&s, &line(&result), &zone);
        }
        let refusals = table.events.iter().filter(|e| e.kind == EventKind::PushRefused).count();
        assert_eq!(refusals, 1);
    }

    #[test]
    fn a_result_clears_its_call_and_errors_make_a_streak() {
        let mut table = Table::default();
        let zone = limits::Zone::fixed("Europe/Lisbon", 0);
        let s = source();
        for (i, at) in ["10:00", "10:01", "10:02"].iter().enumerate() {
            table.ingest(&s, &line(&response(&format!("2026-10-26T{at}:00Z"), &format!("m{i}"), 10, 10, 1, Some("make"))), &zone);
            let result = format!(
                r#"{{"type":"user","timestamp":"2026-10-26T{at}:30Z","message":{{"content":[{{"type":"tool_result","tool_use_id":"t-m{i}","is_error":true,"content":"exit 2"}}]}}}}"#
            );
            table.ingest(&s, &line(&result), &zone);
        }
        let agent = &table.agents["s"];
        assert!(agent.pending.is_empty());
        assert_eq!((agent.error_streak, agent.error_tool.as_str(), &agent.last), (3, "Bash", &Last::Result));
    }

    #[test]
    fn commands_are_normalised_before_they_are_compared() {
        assert_eq!(normalise("gh run view 1234567 --log"), "gh run view # --log");
        assert_eq!(normalise("cat /tmp/x/abc.log | tail -5"), "cat /tmp/* | tail -#");
        assert_eq!(normalise("git show 3fa9c2e1"), "git show #");
        assert!(is_test_command(".venv/bin/python -m pytest tests/ -q") && is_test_command("cd x && pytest -q"));
        assert!(!is_test_command("grep -n 'pytest.fail' tests/conftest.py"));
        assert_eq!(WaitKind::of(&ToolUse { id: "".into(), name: "Bash".into(), command: Some("sleep 600 && gh run view 1".into()), file_path: None, background: false }), WaitKind::Ci);
        assert_eq!(WaitKind::of(&ToolUse { id: "".into(), name: "Bash".into(), command: Some("until test -f x; do sleep 5; done".into()), file_path: None, background: false }), WaitKind::Shell);
    }

    #[test]
    fn files_are_read_from_their_offset_and_a_partial_line_waits() {
        let dir = std::env::temp_dir().join(format!("ling-monitor-read-{}", std::process::id()));
        let subagents = dir.join("p/sess/subagents");
        std::fs::create_dir_all(&subagents).unwrap();
        std::fs::write(dir.join("p/sess.jsonl"), format!("{}\n", response("2026-10-26T10:00:00Z", "m1", 1, 1, 1, None))).unwrap();
        std::fs::write(subagents.join("agent-a1.meta.json"), r#"{"agentType":"Explore","description":"look"}"#).unwrap();
        let partial = response("2026-10-26T10:00:05Z", "m2", 1, 1, 1, None);
        std::fs::write(subagents.join("agent-a1.jsonl"), format!("{}\n{}", response("2026-10-26T10:00:01Z", "m1", 1, 1, 1, None), &partial[..20])).unwrap();
        let mut table = Table::default();
        let found = sources(&dir);
        let mut keys: Vec<_> = found.iter().map(|s| s.key.as_str()).collect();
        keys.sort();
        assert_eq!(keys, vec!["sess", "sess/a1"]);
        let lines = table.read_new(&found, None);
        assert_eq!(lines.len(), 2);
        let zone = limits::Zone::fixed("Europe/Lisbon", 0);
        for (source, line) in &lines {
            table.ingest(source, line, &zone);
        }
        assert_eq!(table.agents["sess/a1"].agent_type, "Explore");
        assert_eq!(table.read_new(&found, None).len(), 0, "the partial line is not read yet");
        let file = subagents.join("agent-a1.jsonl");
        let mut text = std::fs::read_to_string(&file).unwrap();
        text.push_str(&partial[20..]);
        text.push('\n');
        std::fs::write(&file, text).unwrap();
        assert_eq!(table.read_new(&found, None).len(), 1, "the completed line is read once");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
