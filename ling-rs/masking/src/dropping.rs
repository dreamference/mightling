//! Drop-first compaction (specs/research/compaction-survey-2026-10-13.md §3.1; as built:
//! specs/DREAMFERENCE_MIGHTLING_COMPACTION.md §13).
//!
//! When Codex is about to auto-compact, the output of every older tool call is first replaced in
//! the history by a one-line placeholder naming the call: `[output of <cmd> dropped at compaction;
//! re-run if needed]`. The calls, the messages, short outputs and the last [`KEEP_RECENT`] outputs
//! stay as they are. If that brings the context under the limit with room to spare, Codex's summary
//! is skipped; otherwise the history is left whole and the summary runs as before. *The Complexity
//! Trap* (masking matched summaries on SWE-bench Verified) and *CliffCompaction* (truncate, never
//! rephrase) both point here, and on this machine it trades a ~98 s summary for a re-prefill.
//!
//! Codex's core calls [`drop_first`] at the top of `run_auto_compact` (patch `0026`), through
//! `ContextManager::ling_drop_old_outputs`, which rewrites the stored history in place; Codex then
//! re-estimates the context from it, as it does after a summary. Nothing happens until the launcher
//! has called [`set_drop_policy`], so a binary nobody configured behaves as upstream.
//!
//! **The rollout keeps every output.** Only the history in memory is rewritten, so the ledger,
//! the nightly audit and every count of `compacted` records are unaffected, and a drop is not a
//! compaction to any of them. What was dropped is kept per history segment in
//! `$CODEX_HOME/masking/dropped/<key>.json` (the key is the segment's first `call_id`, as for
//! masking), and [`show_dropped`], called from the request hook of patch `0021`, shows those
//! outputs as their placeholders again in a resumed session, whose history the rollout restores in
//! full. Each attempt, taken or declined, is one line of `masking/dropped/log.jsonl`.

use std::collections::BTreeMap;
use std::path::Path;
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use serde::Deserialize;
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;

use super::BYTES_PER_TOKEN;
use super::MIN_MASKABLE_CHARS;
use super::call_id;
use super::clip;
use super::file_name;
use super::is_output;
use super::output_text;
use super::segment_key;

/// The most recent tool outputs always kept whole, short or long: *The Complexity Trap*'s window
/// of 10 turns (its best setting on SWE-bench Verified), the value masking already uses. At the
/// sizes measured on nights 1-4 that is about the survey's "last ~20K tokens".
pub const KEEP_RECENT: usize = 10;
/// Outputs shorter than this are never dropped: they are cheap and often carry the result.
pub const MIN_CHARS: usize = MIN_MASKABLE_CHARS;
/// The share of the limit a drop must leave free to replace the summary. A drop that frees less
/// would be crossed again within a step or two and drop again (Phase 0's thrash, compaction spec
/// §11.2), so then the summary runs instead.
pub const ROOM_PERCENT: u64 = 15;
/// The longest command a placeholder repeats.
pub const MAX_COMMAND_CHARS: usize = 80;
/// The log is started afresh past this size; it gains one line per compaction at most.
const MAX_LOG_BYTES: u64 = 256 * 1024;

/// When a drop replaces the summary.
#[derive(Clone, Debug, PartialEq)]
pub struct DropPolicy {
    /// The compaction limit the session uses, in tokens.
    pub limit: u64,
    /// The most recent tool outputs kept whole.
    pub keep_recent: usize,
    /// Outputs shorter than this are never dropped.
    pub min_chars: usize,
    /// The share of `limit` a drop must leave free.
    pub room_percent: u64,
}

impl DropPolicy {
    /// The defaults for a compaction limit.
    pub fn for_limit(limit: u64) -> Self {
        Self { limit, keep_recent: KEEP_RECENT, min_chars: MIN_CHARS, room_percent: ROOM_PERCENT }
    }

    /// The size a drop must reach for the summary to be skipped.
    pub fn target(&self) -> u64 {
        self.limit * (100 - self.room_percent.min(100)) / 100
    }
}

struct Setup {
    policy: DropPolicy,
    dir: PathBuf,
}

static SETUP: OnceLock<Setup> = OnceLock::new();

/// Switches drop-first compaction on for this process, keeping its files under
/// `codex_home/masking/dropped`. The first call wins; returns whether it was this one.
pub fn set_drop_policy(policy: DropPolicy, codex_home: &Path) -> bool {
    SETUP.set(Setup { policy, dir: dropped_dir(codex_home) }).is_ok()
}

/// The policy in force, if any.
pub fn drop_policy() -> Option<&'static DropPolicy> {
    SETUP.get().map(|setup| &setup.policy)
}

/// Where a Codex home keeps what was dropped.
pub fn dropped_dir(codex_home: &Path) -> PathBuf {
    codex_home.join("masking").join("dropped")
}

/// A segment's dropped outputs: `call_id` to the placeholder shown for it.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct DropState {
    #[serde(default)]
    pub dropped: BTreeMap<String, String>,
}

impl DropState {
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

/// What a drop would do now.
#[derive(Clone, Debug, PartialEq)]
pub struct DropPlan {
    /// The outputs it drops, oldest first, with their placeholders.
    pub newly: Vec<(usize, String)>,
    /// The estimated saving, in tokens.
    pub saving: u64,
    /// The size less the saving.
    pub after: u64,
    /// Whether the drop replaces the summary: it saves something and `after` is at or under the
    /// policy's target.
    pub accepted: bool,
}

/// Drops old tool outputs in place under the process's policy when that makes a summary
/// unnecessary; returns whether it did (and so whether the summary is to be skipped). Without a
/// policy it does nothing and returns `false`. `size` is the context as auto-compaction measures it.
pub fn drop_first<E, T>(items: &mut [E], size: i64, item: impl Fn(&mut E) -> &mut T) -> bool
where
    T: Serialize + DeserializeOwned,
{
    match SETUP.get() {
        Some(setup) => drop_with(&setup.policy, &setup.dir, items, u64::try_from(size).unwrap_or(0), item),
        None => false,
    }
}

/// [`drop_first`] with an explicit policy and folder. A failure to write the state leaves the
/// history as it was and lets the summary run: dropping must never be why a session cannot go on.
pub fn drop_with<E, T>(policy: &DropPolicy, dir: &Path, items: &mut [E], size: u64, item: impl Fn(&mut E) -> &mut T) -> bool
where
    T: Serialize + DeserializeOwned,
{
    let values: Vec<Value> = items
        .iter_mut()
        .map(|entry| serde_json::to_value(&*item(entry)).unwrap_or(Value::Null))
        .collect();
    let Some(key) = segment_key(&values) else { return false };
    let mut state = DropState::load(dir, &key);
    let plan = plan_drop(policy, &values, &state, size);
    log(dir, &key, policy, size, &plan);
    if !plan.accepted {
        return false;
    }
    state.dropped.extend(plan.newly.iter().map(|(index, placeholder)| {
        (call_id(&values[*index]).unwrap_or_default().to_string(), placeholder.clone())
    }));
    if state.save(dir, &key).is_err() {
        return false;
    }
    replace_dropped(&values, &state, items, &item);
    true
}

/// Shows the outputs a segment has dropped as their placeholders. A live session's history already
/// holds them; this matters for a resumed one, whose history the rollout restores in full. Does
/// nothing without a policy, or for a segment that has dropped nothing.
pub fn show_dropped<E, T>(items: &mut [E], item: impl Fn(&mut E) -> &mut T)
where
    T: Serialize + DeserializeOwned,
{
    if let Some(setup) = SETUP.get() {
        show_dropped_with(&setup.dir, items, item);
    }
}

/// [`show_dropped`] with an explicit folder.
pub fn show_dropped_with<E, T>(dir: &Path, items: &mut [E], item: impl Fn(&mut E) -> &mut T)
where
    T: Serialize + DeserializeOwned,
{
    // The key is the first call's id, so only the items up to it are read before knowing whether
    // this segment dropped anything: most requests stop there.
    let mut values: Vec<Value> = Vec::with_capacity(items.len());
    let mut key = None;
    for entry in items.iter_mut() {
        values.push(serde_json::to_value(&*item(entry)).unwrap_or(Value::Null));
        if let Some(found) = segment_key(&values[values.len() - 1..]) {
            key = Some(found);
            break;
        }
    }
    let Some(key) = key else { return };
    let state = DropState::load(dir, &key);
    if state.dropped.is_empty() {
        return;
    }
    let read = values.len();
    values.extend(items[read..].iter_mut().map(|entry| serde_json::to_value(&*item(entry)).unwrap_or(Value::Null)));
    replace_dropped(&values, &state, items, &item);
}

/// Replaces each output the state lists by its placeholder, where it is not one already.
fn replace_dropped<E, T>(values: &[Value], state: &DropState, items: &mut [E], item: &impl Fn(&mut E) -> &mut T)
where
    T: Serialize + DeserializeOwned,
{
    for (index, value) in values.iter().enumerate() {
        if !is_output(value) {
            continue;
        }
        let Some(placeholder) = call_id(value).and_then(|id| state.dropped.get(id)) else { continue };
        if value.get("output").and_then(Value::as_str) == Some(placeholder.as_str()) {
            continue;
        }
        let Value::Object(fields) = value else { continue };
        let mut fields = fields.clone();
        fields.insert("output".to_string(), Value::String(placeholder.clone()));
        if let Ok(dropped) = serde_json::from_value::<T>(Value::Object(fields)) {
            *item(&mut items[index]) = dropped;
        }
    }
}

/// The drop due now: every output outside the last `keep_recent`, at least `min_chars` long and
/// not dropped already, and whether dropping them all brings `size` to the policy's target.
pub fn plan_drop(policy: &DropPolicy, values: &[Value], state: &DropState, size: u64) -> DropPlan {
    let outputs: Vec<usize> = values.iter().enumerate().filter(|(_, value)| is_output(value)).map(|(index, _)| index).collect();
    let older = &outputs[..outputs.len().saturating_sub(policy.keep_recent)];
    let mut newly = Vec::new();
    let mut saving = 0;
    for &index in older {
        let value = &values[index];
        if call_id(value).is_none_or(|id| state.dropped.contains_key(id)) {
            continue;
        }
        let text = output_text(value);
        if text.chars().count() < policy.min_chars {
            continue;
        }
        let placeholder = placeholder_for(values, index);
        let gain = (text.len().saturating_sub(placeholder.len()) as u64) / BYTES_PER_TOKEN;
        if gain == 0 {
            continue;
        }
        saving += gain;
        newly.push((index, placeholder));
    }
    let after = size.saturating_sub(saving);
    DropPlan { accepted: !newly.is_empty() && after <= policy.target(), newly, saving, after }
}

/// The placeholder for the output at `index`: the call's command and, when the output names it,
/// its exit code.
pub fn placeholder_for(values: &[Value], index: usize) -> String {
    let value = &values[index];
    let command = call_id(value)
        .and_then(|id| values[..index].iter().rev().find(|candidate| is_call(candidate) && call_id(candidate) == Some(id)))
        .map(command_of)
        .filter(|command| !command.is_empty())
        .unwrap_or_else(|| "a tool call".to_string());
    let exit = exit_code(&output_text(value)).map(|code| format!(" (exit {code})")).unwrap_or_default();
    format!("[output of {command} dropped at compaction{exit}; re-run if needed]")
}

fn is_call(value: &Value) -> bool {
    matches!(
        value.get("type").and_then(Value::as_str),
        Some("function_call" | "custom_tool_call" | "local_shell_call")
    )
}

/// What a call ran, on one line and at most [`MAX_COMMAND_CHARS`] long: a shell call's command, or
/// the tool's name and its arguments.
pub fn command_of(call: &Value) -> String {
    let name = call.get("name").and_then(Value::as_str).unwrap_or_default();
    let words = |value: &Value| -> Option<String> {
        match value {
            Value::String(text) => Some(text.clone()),
            Value::Array(parts) => Some(parts.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(" ")),
            _ => None,
        }
    };
    let text = match call.get("type").and_then(Value::as_str) {
        Some("function_call") => {
            let arguments = call.get("arguments").and_then(Value::as_str).unwrap_or_default();
            let parsed: Value = serde_json::from_str(arguments).unwrap_or(Value::Null);
            parsed
                .get("cmd")
                .or_else(|| parsed.get("command"))
                .and_then(words)
                .unwrap_or_else(|| format!("{name} {arguments}"))
        }
        Some("custom_tool_call") => format!("{name} {}", call.get("input").and_then(Value::as_str).unwrap_or_default()),
        Some("local_shell_call") => call.pointer("/action/command").and_then(words).unwrap_or_default(),
        _ => name.to_string(),
    };
    clip(&text.split_whitespace().collect::<Vec<_>>().join(" "), MAX_COMMAND_CHARS)
}

/// The exit code an `exec_command` output reports in its header.
fn exit_code(text: &str) -> Option<i64> {
    let header = text.split("Output:\n").next().unwrap_or_default();
    header.lines().find_map(|line| {
        line.strip_prefix("Process exited with code ")
            .or_else(|| line.strip_prefix("Exit code: "))
            .and_then(|code| code.trim().parse().ok())
    })
}

/// One line per attempt in `log.jsonl`: when, which segment, the size, the limit, the saving, how
/// many outputs, and whether the drop replaced the summary. The benchmark counts these.
fn log(dir: &Path, key: &str, policy: &DropPolicy, size: u64, plan: &DropPlan) {
    let line = serde_json::json!({
        "at": SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0),
        "key": key,
        "size": size,
        "limit": policy.limit,
        "saving": plan.saving,
        "after": plan.after,
        "outputs": plan.newly.len(),
        "accepted": plan.accepted,
    });
    let path = dir.join("log.jsonl");
    if std::fs::create_dir_all(dir).is_err() {
        return;
    }
    if std::fs::metadata(&path).is_ok_and(|meta| meta.len() > MAX_LOG_BYTES) {
        let _ = std::fs::rename(&path, dir.join("log.jsonl.old"));
    }
    use std::io::Write;
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(file, "{line}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Codex wraps each item with metadata; the drop must reach through it.
    #[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
    struct Envelope {
        item: Value,
        meta: u32,
    }

    struct Scratch(PathBuf);

    impl Scratch {
        fn new(name: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("ling-dropping-{name}-{}", std::process::id()));
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

    fn exec_output(id: &str, lines: usize, code: i32) -> Value {
        let body: Vec<String> = (0..lines).map(|n| format!("line {n} of {id} {}", "x".repeat(40))).collect();
        json!({"type": "function_call_output", "call_id": id,
               "output": format!("Chunk ID: ab\nWall time: 0.1 seconds\nProcess exited with code {code}\nOriginal token count: 9\nOutput:\n{}", body.join("\n"))})
    }

    /// The system's context, a task message and `calls` calls, each with an output of `lines`
    /// lines (about 55 bytes each, so 20 lines are ~1,100 bytes, ~270 tokens).
    fn history(calls: usize, lines: usize) -> Vec<Envelope> {
        let mut out = vec![
            Envelope { item: json!({"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "rules"}]}), meta: 0 },
            Envelope { item: json!({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix it"}]}), meta: 0 },
        ];
        for n in 0..calls {
            let id = format!("call_{n:03}");
            out.push(Envelope { item: call(&id, &format!("sed -n '1,40p' file{n}.py")), meta: 1 });
            out.push(Envelope { item: exec_output(&id, lines, i32::from(n % 7 == 3)), meta: 2 });
        }
        out
    }

    fn dropped(items: &[Envelope]) -> Vec<usize> {
        items.iter().enumerate().filter(|(_, e)| output_text(&e.item).starts_with("[output of ")).map(|(i, _)| i).collect()
    }

    fn policy() -> DropPolicy {
        DropPolicy::for_limit(44_000)
    }

    #[test]
    fn a_drop_keeps_calls_messages_and_the_last_ten_and_replaces_the_summary() {
        let scratch = Scratch::new("drop");
        // 60 calls of ~270 tokens of output: ~16K tokens of output, of which 50 are droppable.
        let mut items = history(60, 20);
        let before = items.clone();
        assert!(drop_with(&policy(), &scratch.0, &mut items, 44_000, |e| &mut e.item));
        let gone = dropped(&items);
        assert_eq!(gone.len(), 50);
        // Every output but the last ten; the calls and messages are untouched, metadata too.
        assert!(gone.iter().all(|&i| i < items.len() - 20));
        for (index, (now, was)) in items.iter().zip(&before).enumerate() {
            if !gone.contains(&index) {
                assert_eq!(now, was);
            }
            assert_eq!(now.meta, was.meta);
        }
        assert_eq!(output_text(&items[3].item), "[output of sed -n '1,40p' file0.py dropped at compaction (exit 0); re-run if needed]");
        assert!(output_text(&items[9].item).contains("(exit 1)"));
    }

    #[test]
    fn a_drop_that_leaves_too_little_room_changes_nothing_and_lets_the_summary_run() {
        let scratch = Scratch::new("decline");
        let mut items = history(60, 20);
        let before = items.clone();
        // The saving is ~13K tokens; from 100K that is still far over the 37,400 target.
        assert!(!drop_with(&policy(), &scratch.0, &mut items, 100_000, |e| &mut e.item));
        assert_eq!(items, before);
        // Nothing to drop at all: twelve calls, ten of them recent, two short.
        let mut few = history(12, 5);
        assert!(!drop_with(&policy(), &scratch.0, &mut few, 44_000, |e| &mut e.item));
        let log = std::fs::read_to_string(scratch.0.join("log.jsonl")).unwrap_or_default();
        let lines: Vec<Value> = log.lines().filter_map(|line| serde_json::from_str(line).ok()).collect();
        assert_eq!(lines.len(), 2);
        assert!(lines.iter().all(|line| line["accepted"] == json!(false)));
        assert_eq!(lines[0]["outputs"], json!(50));
    }

    #[test]
    fn the_plan_counts_only_outputs_not_dropped_before() {
        let items: Vec<Value> = history(30, 20).into_iter().map(|e| e.item).collect();
        let mut state = DropState::default();
        let first = plan_drop(&policy(), &items, &state, 44_000);
        assert_eq!(first.newly.len(), 20);
        assert!(first.saving > 4_000 && first.after == 44_000 - first.saving);
        state.dropped.insert("call_000".into(), "x".into());
        let second = plan_drop(&policy(), &items, &state, 44_000);
        assert_eq!(second.newly.len(), 19);
        assert!(second.saving < first.saving);
    }

    #[test]
    fn a_resumed_session_is_shown_what_was_dropped() {
        let scratch = Scratch::new("resume");
        let mut live = history(60, 20);
        assert!(drop_with(&policy(), &scratch.0, &mut live, 44_000, |e| &mut e.item));
        // The rollout gives a resumed session its history in full; the request shows the drops.
        let mut resumed = history(60, 20);
        show_dropped_with(&scratch.0, &mut resumed, |e| &mut e.item);
        assert_eq!(resumed, live);
        // Showing again changes nothing, and neither does a segment that dropped nothing.
        show_dropped_with(&scratch.0, &mut resumed, |e| &mut e.item);
        assert_eq!(resumed, live);
        let mut other: Vec<Envelope> = history(60, 20)
            .into_iter()
            .map(|mut e| {
                if let Some(id) = e.item.get("call_id").and_then(Value::as_str).map(|id| id.replace("call_", "other_")) {
                    e.item["call_id"] = json!(id);
                }
                e
            })
            .collect();
        let untouched = other.clone();
        show_dropped_with(&scratch.0, &mut other, |e| &mut e.item);
        assert_eq!(other, untouched);
    }

    #[test]
    fn a_second_drop_adds_to_the_first() {
        let scratch = Scratch::new("second");
        let mut items = history(60, 20);
        assert!(drop_with(&policy(), &scratch.0, &mut items, 44_000, |e| &mut e.item));
        for n in 60..75 {
            let id = format!("call_{n:03}");
            items.push(Envelope { item: call(&id, "pytest -q"), meta: 1 });
            items.push(Envelope { item: exec_output(&id, 20, 1), meta: 2 });
        }
        // The 15 outputs now outside the last ten save ~4.6K tokens: enough from 40K.
        assert!(drop_with(&policy(), &scratch.0, &mut items, 40_000, |e| &mut e.item));
        assert_eq!(dropped(&items).len(), 65);
        let key = segment_key(&items.iter().map(|e| e.item.clone()).collect::<Vec<_>>()).unwrap_or_default();
        assert_eq!(DropState::load(&scratch.0, &key).dropped.len(), 65);
    }

    #[test]
    fn the_placeholder_names_the_command_of_every_kind_of_call() {
        let long = format!("python -c \"{}\"", "print(1);".repeat(30));
        assert_eq!(command_of(&call("c", &long)).chars().count(), MAX_COMMAND_CHARS);
        assert_eq!(command_of(&call("c", "git  diff\n --stat")), "git diff --stat");
        let shell = json!({"type": "function_call", "name": "shell", "arguments": json!({"command": ["bash", "-lc", "ls"]}).to_string(), "call_id": "c"});
        assert_eq!(command_of(&shell), "bash -lc ls");
        let tool = json!({"type": "function_call", "name": "code_show", "arguments": "{\"name\":\"solveset\"}", "call_id": "c"});
        assert_eq!(command_of(&tool), "code_show {\"name\":\"solveset\"}");
        let custom = json!({"type": "custom_tool_call", "name": "exec", "input": "const r = await tools.exec_command({cmd: 'ls'})", "call_id": "c"});
        assert!(command_of(&custom).starts_with("exec const r = await"));
        let items = vec![
            json!({"type": "function_call_output", "call_id": "orphan", "output": "x".repeat(700)}),
        ];
        assert_eq!(placeholder_for(&items, 0), "[output of a tool call dropped at compaction; re-run if needed]");
    }

    #[test]
    fn old_drop_states_are_pruned_with_maskings() {
        let scratch = Scratch::new("prune");
        let home = scratch.0.clone();
        let state = DropState { dropped: [("call_1".to_string(), "x".to_string())].into() };
        state.save(&dropped_dir(&home), "call_first").unwrap_or_default();
        let file = dropped_dir(&home).join("call_first.json");
        crate::prune(&home, SystemTime::now());
        assert!(file.exists());
        crate::prune(&home, SystemTime::now() + std::time::Duration::from_secs((crate::KEEP_DAYS + 1) * 86_400));
        assert!(!file.exists());
    }

    #[test]
    fn without_a_policy_nothing_changes() {
        // No test calls set_drop_policy, so the process has none.
        let mut items = history(60, 20);
        let before = items.clone();
        assert!(!drop_first(&mut items, 1_000_000, |e| &mut e.item));
        show_dropped(&mut items, |e| &mut e.item);
        assert_eq!(items, before);
    }

    #[test]
    fn a_room_share_of_the_whole_limit_or_more_leaves_no_target() {
        let policy = DropPolicy { room_percent: 150, ..policy() };
        assert_eq!(policy.target(), 0);
        assert_eq!(DropPolicy { room_percent: 100, ..policy }.target(), 0);
    }

    #[test]
    fn a_drop_whose_state_cannot_be_saved_changes_nothing() {
        let scratch = Scratch::new("unsaved");
        // A file where the folder belongs: neither the state nor the log can be written.
        std::fs::write(&scratch.0, "in the way").unwrap_or_default();
        let mut items = history(60, 20);
        let before = items.clone();
        assert!(!drop_with(&policy(), &scratch.0, &mut items, 44_000, |e| &mut e.item));
        assert_eq!(items, before);
        let _ = std::fs::remove_file(&scratch.0);
    }

    #[test]
    fn outputs_shorter_than_the_minimum_stay_and_one_of_exactly_the_minimum_goes() {
        let items: Vec<Value> = history(12, 5).into_iter().map(|e| e.item).collect();
        assert!(output_text(&items[3]).chars().count() < crate::MIN_MASKABLE_CHARS);
        assert!(plan_drop(&policy(), &items, &DropState::default(), 44_000).newly.is_empty());
        let edge = vec![call("c", "ls"), json!({"type": "function_call_output", "call_id": "c", "output": "y".repeat(MIN_CHARS)})];
        let policy = DropPolicy { keep_recent: 0, ..policy() };
        assert_eq!(plan_drop(&policy, &edge, &DropState::default(), 44_000).newly.len(), 1);
    }

    #[test]
    fn an_output_no_longer_than_its_placeholder_is_not_dropped() {
        let policy = DropPolicy { keep_recent: 0, min_chars: 0, ..policy() };
        let items = vec![call("c", "ls"), json!({"type": "function_call_output", "call_id": "c", "output": "ok"})];
        let plan = plan_drop(&policy, &items, &DropState::default(), 44_000);
        assert_eq!((plan.newly.len(), plan.saving, plan.accepted), (0, 0, false));
    }
}
