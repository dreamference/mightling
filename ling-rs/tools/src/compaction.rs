//! The compaction request offers the tools the thread's last request offered.
//!
//! Codex builds its local compaction request without tools (`core/src/compact.rs`: `Prompt {
//! input, base_instructions, ..Default::default() }`), and the served chat template renders the
//! tool list first (`<|im_start|>system\n# Tools…`). The compaction prompt therefore differs from
//! the turn's within its first few tokens, and the model server re-reads the whole context, 62–91K
//! tokens at ~1,350 tok/s, before the summary starts: 47–75 s of every compaction, turn-end and
//! mid-turn, measured on 2026-10-13 (`docs/dev/turn-end-compaction/2026-10-13.md`, 0 cached tokens
//! in 12 of 13 compactions).
//!
//! Patch 0027 hands every request's tools to [`same_tools`] with the request's thread id and
//! whether it is a compaction. A request that offers tools is remembered for its thread; a
//! compaction that offers none gets the remembered list, byte for byte, so its prompt extends the
//! one the server holds. Nothing else changes: a tool-less request that is not a compaction (the
//! memory extraction, a title) neither gets tools nor replaces what was remembered, and another
//! thread (a sub-agent) has its own list. The compaction prompt still asks for a summary; Codex
//! keeps the response's message items as it always did.
//!
//! `DREAMFERENCE_MIGHTLING_COMPACTION_TOOLS=0` switches it off (read once per process).

use std::sync::Arc;
use std::sync::Mutex;
use std::sync::OnceLock;

use serde_json::value::RawValue;

/// Switches the substitution off when set to `0`, `false`, `off` or `no`.
pub const SWITCH_ENV: &str = "DREAMFERENCE_MIGHTLING_COMPACTION_TOOLS";

/// Threads whose tools are kept, most recent first; an app server holds a few at once.
const KEPT_THREADS: usize = 16;

fn kept() -> &'static Mutex<Vec<(String, Arc<RawValue>)>> {
    static KEPT: OnceLock<Mutex<Vec<(String, Arc<RawValue>)>>> = OnceLock::new();
    KEPT.get_or_init(Mutex::default)
}

fn enabled() -> bool {
    static ENABLED: OnceLock<bool> = OnceLock::new();
    *ENABLED.get_or_init(|| {
        let value = std::env::var(SWITCH_ENV).unwrap_or_default();
        !matches!(value.trim().to_ascii_lowercase().as_str(), "0" | "false" | "off" | "no")
    })
}

fn is_empty(tools: &RawValue) -> bool {
    tools.get().trim() == "[]"
}

/// The tools to send with a request (patch 0027, after [`crate::for_server`]): `tools` as they
/// came, except that a compaction offering none gets the list the same thread's last request
/// offered.
pub fn same_tools(tools: Arc<RawValue>, thread: &str, compaction: bool) -> Arc<RawValue> {
    if enabled() { same_tools_with(tools, thread, compaction) } else { tools }
}

/// [`same_tools`] regardless of the switch.
pub fn same_tools_with(tools: Arc<RawValue>, thread: &str, compaction: bool) -> Arc<RawValue> {
    let empty = is_empty(&tools);
    if compaction != empty {
        // A compaction that already has tools (the remote path), or a tool-less side request.
        return tools;
    }
    let mut kept = kept().lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let position = kept.iter().position(|(id, _)| id == thread);
    if compaction {
        return position.map_or(tools, |index| Arc::clone(&kept[index].1));
    }
    if let Some(index) = position {
        kept.remove(index);
    }
    kept.insert(0, (thread.to_string(), Arc::clone(&tools)));
    kept.truncate(KEPT_THREADS);
    tools
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use serde_json::json;

    fn raw(value: Value) -> Arc<RawValue> {
        Arc::from(serde_json::value::to_raw_value(&value).unwrap_or_else(|_| unreachable!()))
    }

    fn turn_tools() -> Arc<RawValue> {
        raw(json!([
            {"type": "function", "name": "exec_command", "description": "Runs a command", "parameters": {"type": "object"}},
            {"type": "function", "name": "apply_patch", "description": "Edits files"},
            {"type": "function", "name": "code_def", "description": "Where a name is defined."},
            {"type": "web_search"},
        ]))
    }

    #[test]
    fn a_compaction_offers_the_turns_tools_in_the_same_order() {
        let turn = turn_tools();
        assert!(Arc::ptr_eq(&same_tools_with(Arc::clone(&turn), "thread-a", false), &turn));
        let compaction = same_tools_with(raw(json!([])), "thread-a", true);
        // Byte for byte: the rendered system block, and so the cached prefix, is the turn's.
        assert_eq!(compaction.get(), turn.get());
    }

    #[test]
    fn the_latest_turn_wins() {
        same_tools_with(turn_tools(), "thread-b", false);
        let later = raw(json!([{"type": "function", "name": "exec_command"}, {"type": "function", "name": "code_refs"}]));
        same_tools_with(Arc::clone(&later), "thread-b", false);
        assert_eq!(same_tools_with(raw(json!([])), "thread-b", true).get(), later.get());
    }

    #[test]
    fn a_side_request_neither_gets_tools_nor_replaces_them() {
        let turn = turn_tools();
        same_tools_with(Arc::clone(&turn), "thread-c", false);
        // A tool-less request that is not a compaction (memory extraction, a title) stays tool-less.
        assert_eq!(same_tools_with(raw(json!([])), "thread-c", false).get(), "[]");
        // ...and the compaction after it still gets the turn's tools.
        assert_eq!(same_tools_with(raw(json!([])), "thread-c", true).get(), turn.get());
    }

    #[test]
    fn threads_are_kept_apart() {
        same_tools_with(turn_tools(), "thread-d", false);
        // A thread that never sent tools (a sub-agent's first request is a compaction) gets none.
        assert_eq!(same_tools_with(raw(json!([])), "thread-e", true).get(), "[]");
    }

    #[test]
    fn a_compaction_that_has_tools_keeps_them() {
        same_tools_with(turn_tools(), "thread-f", false);
        let own = raw(json!([{"type": "function", "name": "other"}]));
        assert!(Arc::ptr_eq(&same_tools_with(Arc::clone(&own), "thread-f", true), &own));
    }

    #[test]
    fn only_the_most_recent_threads_are_kept() {
        for index in 0..KEPT_THREADS + 4 {
            same_tools_with(turn_tools(), &format!("bounded-{index}"), false);
        }
        assert!(kept().lock().map(|kept| kept.len() <= KEPT_THREADS).unwrap_or(false));
        assert_eq!(same_tools_with(raw(json!([])), &format!("bounded-{}", KEPT_THREADS + 3), true).get(), turn_tools().get());
    }
}
