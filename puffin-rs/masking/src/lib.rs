//! Observation masking (specs/DREAMFERENCE_PUFFIN_CONTEXT_BUDGET.md §4.1).
//!
//! Once the request nears the compaction limit, the output of the oldest tool calls is replaced in
//! what is sent by a placeholder under 200 characters naming a saved copy of it, so the model can
//! read it back with `sed -n` instead of running the command again. Commands, messages, short
//! outputs and the last few outputs are never touched. The rollout file still records every output
//! in full: this changes what is sent, not what is stored.
//!
//! Codex's core calls [`apply`] at the end of `ContextManager::for_prompt_annotated` (patch
//! `0021`), which builds both the sampling request and the compaction request, and passes the size
//! auto-compaction itself compares with the limit: the server's count for the last request plus
//! Codex's estimate of what was recorded since (`get_total_token_usage`). Nothing happens until the
//! launcher has called [`set_policy`], so a binary nobody configured behaves as upstream.
//!
//! **A move, then nothing until the next.** When the size passes the high mark and has grown by
//! the minimum step since the last move, the oldest maskable outputs are masked until the estimated
//! saving covers the size minus the low mark. Between moves the masked set does not change, so the
//! model server's prefix cache holds.
//!
//! **The boundary is stored.** Past server counts are not in the history, so the masked set and the
//! size after the last move are kept in `$CODEX_HOME/masking/<key>.json`, where the key is the
//! `call_id` of the history segment's first tool call. A resumed session reads the same file and
//! sends the same view; a compaction rewrites the history, which starts a new segment and file.
//!
//! Items are read through serde as JSON in the Responses API's shape (`type`, `call_id`,
//! `output`), so this crate needs none of Codex's types.

use std::collections::BTreeSet;
use std::collections::HashMap;
use std::path::Path;
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::Duration;
use std::time::SystemTime;

use serde::Deserialize;
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;

/// The high mark, as a percentage of the compaction limit.
pub const HIGH_PERCENT: u64 = 85;
/// The low mark, as a percentage of the compaction limit.
pub const LOW_PERCENT: u64 = 50;
/// Tokens the size must grow by after a move before the next one. Each move re-prefills about
/// everything after the first masked item, so few large moves beat many small ones (§1.7).
pub const MIN_STEP_TOKENS: u64 = 16_000;
/// The most recent maskable outputs that are always kept whole.
pub const KEEP_RECENT: usize = 10;
/// Outputs shorter than this are never masked: they are cheap and often carry the result.
pub const MIN_MASKABLE_CHARS: usize = 600;
/// The longest placeholder: at 400 characters masking gave back most of its gain (§4.1).
pub const MAX_PLACEHOLDER_CHARS: usize = 200;
/// Bytes per token in the saving's estimate, as Codex's own estimator counts. It reads low on this
/// model, so a move masks a little more than it must: the safe direction.
pub const BYTES_PER_TOKEN: u64 = 4;
/// Days a segment's state and saved outputs are kept, counted from the state file's last write.
pub const KEEP_DAYS: u64 = 30;

/// The longest last line a placeholder keeps, and its first line.
const LAST_LINE_CHARS: usize = 80;
const FIRST_LINE_CHARS: usize = 60;

/// When and how much to mask.
#[derive(Clone, Debug, PartialEq)]
pub struct Policy {
    /// A move needs the size above this many tokens.
    pub high: u64,
    /// A move masks until the size, less the estimated saving, is at or under this.
    pub low: u64,
    /// A move needs the size above the size after the last move plus this.
    pub min_step: u64,
    /// The most recent maskable outputs kept whole.
    pub keep_recent: usize,
    /// Outputs shorter than this are never masked.
    pub min_chars: usize,
}

impl Policy {
    /// The defaults for a compaction limit.
    pub fn for_limit(limit: u64) -> Self {
        Self::with_shares(limit, HIGH_PERCENT, LOW_PERCENT, MIN_STEP_TOKENS)
    }

    /// Marks at the given percentages of `limit`, and a minimum step in tokens.
    pub fn with_shares(limit: u64, high_percent: u64, low_percent: u64, min_step: u64) -> Self {
        Self {
            high: limit * high_percent / 100,
            low: limit * low_percent / 100,
            min_step,
            keep_recent: KEEP_RECENT,
            min_chars: MIN_MASKABLE_CHARS,
        }
    }
}

struct Setup {
    policy: Policy,
    dir: PathBuf,
}

static SETUP: OnceLock<Setup> = OnceLock::new();

/// Switches masking on for this process, keeping its files under `codex_home/masking`. The first
/// call wins; returns whether it was this one.
pub fn set_policy(policy: Policy, codex_home: &Path) -> bool {
    SETUP.set(Setup { policy, dir: codex_home.join("masking") }).is_ok()
}

/// The policy in force, if any.
pub fn policy() -> Option<&'static Policy> {
    SETUP.get().map(|setup| &setup.policy)
}

/// A segment's state: what is masked, and the size after the last move.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct State {
    pub masked: BTreeSet<String>,
    pub after_last_move: Option<u64>,
}

impl State {
    fn path(dir: &Path, key: &str) -> PathBuf {
        dir.join(format!("{}.json", file_name(key)))
    }

    /// The segment's state, or an empty one when there is none or it does not read.
    pub fn load(dir: &Path, key: &str) -> Self {
        std::fs::read_to_string(Self::path(dir, key))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    /// Replaces the segment's state file whole.
    pub fn save(&self, dir: &Path, key: &str) -> std::io::Result<()> {
        std::fs::create_dir_all(dir)?;
        let path = Self::path(dir, key);
        let staging = path.with_extension("json.tmp");
        std::fs::write(&staging, serde_json::to_vec(self).unwrap_or_default())?;
        std::fs::rename(staging, path)
    }
}

/// Masks the history in place under the process's policy; does nothing without one. `size` is the
/// request's size as auto-compaction measures it. `item` reaches the response item inside whatever
/// wraps it (Codex keeps metadata beside each item).
pub fn apply<E, T>(items: &mut [E], size: i64, item: impl Fn(&mut E) -> &mut T)
where
    T: Serialize + DeserializeOwned,
{
    if let Some(setup) = SETUP.get() {
        apply_with(&setup.policy, &setup.dir, items, u64::try_from(size).unwrap_or(0), item);
    }
}

/// [`apply`] with an explicit policy and folder. Every failure leaves the item as it was: masking
/// must never be the reason a request cannot be sent.
pub fn apply_with<E, T>(policy: &Policy, dir: &Path, items: &mut [E], size: u64, item: impl Fn(&mut E) -> &mut T)
where
    T: Serialize + DeserializeOwned,
{
    let values: Vec<Value> = items
        .iter_mut()
        .map(|entry| serde_json::to_value(&*item(entry)).unwrap_or(Value::Null))
        .collect();
    let Some(key) = segment_key(&values) else { return };
    let mut state = State::load(dir, &key);
    if let Some(newly) = plan_move(policy, &values, &state, size, dir) {
        let saved: Vec<String> = newly
            .into_iter()
            .filter(|&index| save_copy(dir, &values[index]).is_ok())
            .filter_map(|index| call_id(&values[index]).map(str::to_string))
            .collect();
        let saving: u64 = values
            .iter()
            .filter(|value| call_id(value).is_some_and(|id| saved.iter().any(|s| s == id)))
            .map(|value| saving_tokens(value, dir))
            .sum();
        state.masked.extend(saved);
        state.after_last_move = Some(size.saturating_sub(saving));
        let _ = state.save(dir, &key);
    }
    if state.masked.is_empty() {
        return;
    }
    for (index, value) in values.iter().enumerate() {
        let Some(id) = call_id(value).filter(|id| is_output(value) && state.masked.contains(*id)) else {
            continue;
        };
        let Value::Object(fields) = value else { continue };
        let mut fields = fields.clone();
        fields.insert("output".to_string(), Value::String(placeholder(&output_text(value), &copy_path(dir, id))));
        if let Ok(masked) = serde_json::from_value::<T>(Value::Object(fields)) {
            *item(&mut items[index]) = masked;
        }
    }
}

/// The outputs a move masks now, oldest first, or `None` when no move is due.
pub fn plan_move(policy: &Policy, values: &[Value], state: &State, size: u64, dir: &Path) -> Option<Vec<usize>> {
    let trigger = policy.high.max(state.after_last_move.map_or(0, |after| after + policy.min_step));
    if size <= trigger {
        return None;
    }
    let candidates: Vec<usize> = values
        .iter()
        .enumerate()
        .filter(|(_, value)| is_output(value))
        .filter(|(_, value)| call_id(value).is_some_and(|id| !state.masked.contains(id)))
        .filter(|(_, value)| output_text(value).chars().count() >= policy.min_chars)
        .map(|(index, _)| index)
        .collect();
    let movable = candidates.len().saturating_sub(policy.keep_recent);
    let needed = size.saturating_sub(policy.low);
    let mut saving = 0;
    let mut out = Vec::new();
    for &index in &candidates[..movable] {
        if saving >= needed {
            break;
        }
        let gain = saving_tokens(&values[index], dir);
        if gain == 0 {
            continue;
        }
        saving += gain;
        out.push(index);
    }
    Some(out)
}

/// What masking one output saves, in estimated tokens: its text less its placeholder.
fn saving_tokens(value: &Value, dir: &Path) -> u64 {
    let text = output_text(value);
    let path = call_id(value).map(|id| copy_path(dir, id)).unwrap_or_default();
    let kept = placeholder(&text, &path).len();
    (text.len().saturating_sub(kept) as u64) / BYTES_PER_TOKEN
}

/// The placeholder: the size and the saved copy's path, the output's first line (the exit line, or
/// a tool's own first line) and its last line, within [`MAX_PLACEHOLDER_CHARS`].
pub fn placeholder(text: &str, path: &Path) -> String {
    let (header, body) = match text.find("Output:\n") {
        Some(at) => (&text[..at], &text[at + "Output:\n".len()..]),
        None => ("", text),
    };
    let lines: Vec<&str> = body.trim_end().lines().filter(|line| !line.trim().is_empty()).collect();
    let first = header
        .lines()
        .find(|line| line.starts_with("Process exited") || line.starts_with("Exit code"))
        .or_else(|| lines.first().copied())
        .unwrap_or_default();
    let mut out = format!("[output moved out of context, {} chars: {}]", text.chars().count(), path.display());
    let mut add = |line: &str, prefix: &str, limit: usize| {
        let room = MAX_PLACEHOLDER_CHARS.saturating_sub(out.chars().count() + 1 + prefix.chars().count());
        if room >= 12 {
            out.push('\n');
            out.push_str(prefix);
            out.push_str(&clip(line, limit.min(room)));
        }
    };
    if !first.is_empty() {
        add(first, "", FIRST_LINE_CHARS);
    }
    if let Some(last) = lines.last().filter(|last| lines.len() > 1 || **last != first) {
        add(last, "… ", LAST_LINE_CHARS);
    }
    out
}

/// Writes an output's text to its saved copy, once.
pub fn save_copy(dir: &Path, value: &Value) -> std::io::Result<()> {
    let id = call_id(value).ok_or_else(|| std::io::Error::other("no call_id"))?;
    let path = copy_path(dir, id);
    if path.is_file() {
        return Ok(());
    }
    std::fs::create_dir_all(path.parent().unwrap_or(dir))?;
    let staging = path.with_extension("txt.tmp");
    std::fs::write(&staging, output_text(value))?;
    std::fs::rename(staging, path)
}

/// Where an output's saved copy lives.
pub fn copy_path(dir: &Path, call_id: &str) -> PathBuf {
    dir.join("outputs").join(format!("{}.txt", file_name(call_id)))
}

/// The segment's key: the `call_id` of its first tool call.
pub fn segment_key(values: &[Value]) -> Option<String> {
    values
        .iter()
        .find(|value| {
            matches!(
                value.get("type").and_then(Value::as_str),
                Some("function_call" | "custom_tool_call" | "local_shell_call")
            )
        })
        .and_then(call_id)
        .map(str::to_string)
}

/// Deletes segment files not written for [`KEEP_DAYS`] days, with the saved outputs they name.
/// Returns how many segments went.
pub fn prune(codex_home: &Path, now: SystemTime) -> usize {
    let dir = codex_home.join("masking");
    let Ok(entries) = std::fs::read_dir(&dir) else { return 0 };
    let mut pruned = 0;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().is_none_or(|ext| ext != "json") {
            continue;
        }
        let old = entry
            .metadata()
            .and_then(|meta| meta.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age > Duration::from_secs(KEEP_DAYS * 86_400));
        if !old {
            continue;
        }
        let state: State = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();
        for id in &state.masked {
            let _ = std::fs::remove_file(copy_path(&dir, id));
        }
        if std::fs::remove_file(&path).is_ok() {
            pruned += 1;
        }
    }
    pruned
}

fn call_id(value: &Value) -> Option<&str> {
    value.get("call_id").and_then(Value::as_str)
}

fn is_output(value: &Value) -> bool {
    matches!(
        value.get("type").and_then(Value::as_str),
        Some("function_call_output" | "custom_tool_call_output")
    )
}

/// An output's text: the string itself, or its content items' texts joined by newlines.
pub fn output_text(value: &Value) -> String {
    match value.get("output") {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Array(parts)) => parts
            .iter()
            .filter_map(|part| part.get("text").and_then(Value::as_str))
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

/// A `call_id` as a file name: anything but ASCII letters, digits, `-` and `_` becomes `_`.
fn file_name(id: &str) -> String {
    id.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' }).collect()
}

fn clip(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_string();
    }
    let mut out: String = text.chars().take(limit.saturating_sub(1)).collect();
    out.push('…');
    out
}

/// What masking a history would look like, for tests and the replay: the indices `apply_with`
/// shows as placeholders.
pub fn masked_indices(values: &[Value], state: &State) -> HashMap<usize, String> {
    values
        .iter()
        .enumerate()
        .filter(|(_, value)| is_output(value))
        .filter_map(|(index, value)| call_id(value).filter(|id| state.masked.contains(*id)).map(|id| (index, id.to_string())))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Codex wraps each item with metadata; the masking must reach through it.
    #[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
    struct Envelope {
        item: Value,
        meta: u32,
    }

    struct Scratch(PathBuf);

    impl Scratch {
        fn new(name: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("puffin-masking-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&dir);
            Self(dir)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn call(id: &str, cmd: &str) -> Value {
        json!({"type": "function_call", "name": "exec_command", "arguments": json!({"cmd": cmd}).to_string(), "call_id": id})
    }

    fn exec_output(id: &str, lines: usize) -> Value {
        let body: Vec<String> = (0..lines).map(|n| format!("line {n} of {id} {}", "x".repeat(40))).collect();
        json!({"type": "function_call_output", "call_id": id,
               "output": format!("Chunk ID: ab\nWall time: 0.1 seconds\nProcess exited with code 0\nOriginal token count: 9\nOutput:\n{}", body.join("\n"))})
    }

    /// A task message and `calls` calls, each with an output of `lines` lines (about 55 bytes each).
    fn history(calls: usize, lines: usize) -> Vec<Envelope> {
        let mut out = vec![Envelope { item: json!({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix it"}]}), meta: 0 }];
        for n in 0..calls {
            let id = format!("call_{n:03}");
            out.push(Envelope { item: call(&id, &format!("sed -n '1,40p' file{n}.py")), meta: 1 });
            out.push(Envelope { item: exec_output(&id, lines), meta: 2 });
        }
        out
    }

    fn masked(items: &[Envelope]) -> Vec<usize> {
        items
            .iter()
            .enumerate()
            .filter(|(_, e)| output_text(&e.item).starts_with("[output moved out of context"))
            .map(|(i, _)| i)
            .collect()
    }

    fn policy() -> Policy {
        Policy::for_limit(44_000)
    }

    fn run(dir: &Path, items: &mut [Envelope], size: u64) {
        apply_with(&policy(), dir, items, size, |e| &mut e.item);
    }

    #[test]
    fn nothing_is_masked_below_the_high_mark() {
        let scratch = Scratch::new("below");
        let mut items = history(40, 20);
        let before = items.clone();
        run(&scratch.0, &mut items, policy().high);
        assert_eq!(items, before);
        assert!(!scratch.0.exists(), "no move, no files");
    }

    #[test]
    fn a_move_masks_oldest_first_until_the_saving_covers_the_size_less_the_low_mark() {
        let scratch = Scratch::new("move");
        let mut items = history(40, 60);
        let size = policy().high + 1;
        run(&scratch.0, &mut items, size);
        let gone = masked(&items);
        assert_eq!(gone[0], 2, "the first output is the oldest");
        assert!(gone.windows(2).all(|pair| pair[1] == pair[0] + 2), "contiguous from the oldest: {gone:?}");
        let saving: u64 = gone
            .iter()
            .map(|&i| {
                let original = exec_output(&format!("call_{:03}", (i - 2) / 2), 60);
                saving_tokens(&original, &scratch.0)
            })
            .sum();
        assert!(saving >= size - policy().low, "saving {saving}");
        let one_less = saving - saving_tokens(&exec_output("call_000", 60), &scratch.0);
        assert!(one_less < size - policy().low, "one output fewer would not have been enough");
        // Calls, messages and metadata are untouched.
        for (index, entry) in items.iter().enumerate() {
            assert_eq!(entry.meta, if index == 0 { 0 } else if index % 2 == 1 { 1 } else { 2 });
        }
    }

    #[test]
    fn the_last_ten_and_short_outputs_are_never_masked() {
        let scratch = Scratch::new("exempt");
        let mut items = history(30, 20);
        // A short output in the middle (call n's output is at 2 + 2n).
        items[22].item = json!({"type": "function_call_output", "call_id": "call_010", "output": "Process exited with code 0\nOutput:\nok"});
        run(&scratch.0, &mut items, 1_000_000);
        let gone = masked(&items);
        assert!(!gone.contains(&22));
        let outputs: Vec<usize> = (0..items.len()).filter(|&i| is_output(&items[i].item)).collect();
        for kept in &outputs[outputs.len() - KEEP_RECENT..] {
            assert!(!gone.contains(kept), "output {kept} is among the last {KEEP_RECENT}");
        }
        assert_eq!(gone.len(), 30 - 1 - KEEP_RECENT, "everything else goes when the low mark is far");
    }

    #[test]
    fn no_second_move_before_the_minimum_step() {
        let scratch = Scratch::new("step");
        let mut items = history(60, 60);
        let size = policy().high + 1;
        run(&scratch.0, &mut items, size);
        let first = masked(&items);
        let key = segment_key(&items.iter().map(|e| e.item.clone()).collect::<Vec<_>>()).unwrap_or_default();
        let after = State::load(&scratch.0, &key).after_last_move.unwrap_or_default();
        assert!(after < size);
        // Grown, but not by the step since the last move: the same view.
        let mut again = history(60, 60);
        run(&scratch.0, &mut again, (after + MIN_STEP_TOKENS).max(policy().high));
        assert_eq!(masked(&again), first);
        // Past the step: another move.
        let mut later = history(60, 60);
        run(&scratch.0, &mut later, after + MIN_STEP_TOKENS + policy().high);
        assert!(masked(&later).len() > first.len());
    }

    #[test]
    fn the_view_is_the_stored_set_and_survives_a_new_process() {
        let scratch = Scratch::new("resume");
        let mut items = history(40, 20);
        run(&scratch.0, &mut items, policy().high + 1);
        let first = masked(&items);
        // A resumed session: same history, below the next trigger, same view.
        let mut resumed = history(40, 20);
        run(&scratch.0, &mut resumed, policy().high + 1);
        assert_eq!(resumed, items);
        // After a compaction the history is a new segment: its own key, nothing masked.
        let mut compacted = history(0, 0);
        compacted.push(Envelope { item: call("call_new", "ls"), meta: 1 });
        compacted.push(Envelope { item: exec_output("call_new", 20), meta: 2 });
        run(&scratch.0, &mut compacted, 0);
        assert!(masked(&compacted).is_empty());
        assert!(!first.is_empty());
    }

    #[test]
    fn ids_no_longer_in_the_history_are_ignored() {
        let scratch = Scratch::new("stale");
        let mut items = history(5, 20);
        let key = segment_key(&items.iter().map(|e| e.item.clone()).collect::<Vec<_>>()).unwrap_or_default();
        let state = State { masked: ["call_999".to_string(), "call_001".to_string()].into(), after_last_move: Some(1) };
        state.save(&scratch.0, &key).unwrap_or_default();
        let _ = save_copy(&scratch.0, &exec_output("call_001", 20));
        run(&scratch.0, &mut items, 0);
        assert_eq!(masked(&items), vec![4]);
    }

    #[test]
    fn the_saved_copy_is_written_once_and_holds_the_output_exactly() {
        let scratch = Scratch::new("copy");
        let output = exec_output("call_abc", 30);
        save_copy(&scratch.0, &output).unwrap_or_default();
        let path = copy_path(&scratch.0, "call_abc");
        assert_eq!(std::fs::read_to_string(&path).unwrap_or_default(), output_text(&output));
        std::fs::write(&path, "kept").unwrap_or_default();
        save_copy(&scratch.0, &exec_output("call_abc", 3)).unwrap_or_default();
        assert_eq!(std::fs::read_to_string(&path).unwrap_or_default(), "kept");
    }

    #[test]
    fn the_placeholder_names_the_copy_keeps_the_exit_and_last_lines_and_stays_short() {
        let path = Path::new("/home/user/.puffin/masking/outputs/call_ca1c38ae56a14f70ae27cdc6.txt");
        let output = exec_output("x", 30);
        let text = placeholder(&output_text(&output), path);
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines[0], format!("[output moved out of context, {} chars: {}]", output_text(&output).chars().count(), path.display()));
        assert_eq!(lines[1], "Process exited with code 0");
        assert!(lines[2].starts_with("… line 29 of x"));
        assert!(text.chars().count() <= MAX_PLACEHOLDER_CHARS, "{}", text.chars().count());
    }

    #[test]
    fn a_tools_answer_keeps_its_own_first_line_and_becomes_text() {
        let scratch = Scratch::new("mcp");
        let body: Vec<String> = (0..40).map(|n| format!("{n:4}  code line {}", "y".repeat(30))).collect();
        let mut items: Vec<Value> = Vec::new();
        for n in 0..30 {
            let id = format!("call_m{n:02}");
            items.push(json!({"type": "function_call", "name": "code_show", "arguments": "{}", "call_id": id}));
            items.push(json!({"type": "function_call_output", "call_id": id, "output": [
                {"type": "input_text", "text": "Wall time: 0.1 seconds\nOutput:"},
                {"type": "input_text", "text": format!("show solveset (sympy/solvers/solveset.py:1962-2126)\n{}", body.join("\n"))}
            ]}));
        }
        apply_with(&policy(), &scratch.0, &mut items, 1_000_000, |v| v);
        let first = items[1]["output"].as_str().unwrap_or_default();
        assert_eq!(first.lines().nth(1), Some("show solveset (sympy/solvers/solveset.py:1962-2126)"));
        assert!(first.chars().count() <= MAX_PLACEHOLDER_CHARS);
        let saved = std::fs::read_to_string(copy_path(&scratch.0, "call_m00")).unwrap_or_default();
        assert!(saved.contains("show solveset") && saved.contains("  39  code line"));
        assert!(items[items.len() - 1]["output"].is_array());
    }

    #[test]
    fn old_segments_are_pruned_with_their_outputs() {
        let scratch = Scratch::new("prune");
        let home = scratch.0.clone();
        let dir = home.join("masking");
        let state = State { masked: ["call_1".to_string()].into(), after_last_move: Some(1) };
        state.save(&dir, "call_first").unwrap_or_default();
        save_copy(&dir, &exec_output("call_1", 20)).unwrap_or_default();
        assert_eq!(prune(&home, SystemTime::now()), 0);
        let later = SystemTime::now() + Duration::from_secs((KEEP_DAYS + 1) * 86_400);
        assert_eq!(prune(&home, later), 1);
        assert!(!copy_path(&dir, "call_1").exists());
    }

    #[test]
    fn without_a_policy_nothing_changes() {
        // No test calls set_policy, so the process has none.
        let mut items = history(60, 20);
        let before = items.clone();
        apply(&mut items, 1_000_000, |e| &mut e.item);
        assert_eq!(items, before);
    }
}
