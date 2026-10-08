//! Whether old tool outputs are masked in what is sent, and at what sizes
//! (specs/DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md §4.1).
//!
//! The rule itself is the leaf crate `ling-rs/masking`, which Codex's core calls through patch
//! `0021`. This module decides once per launch whether it runs, and hands it the marks, derived
//! from the compaction limit the session will actually use: a `-c` on the command line (Night
//! Shift and SWE-bench pass their own), then `config.toml`, then the model's window. Off by default
//! until the A/B of the spec's §6 decides; `DREAMFERENCE_MIGHTLING_MASK` or `mightling_mask_tool_output`
//! in the config file switches it on, and `[mightling_mask]` there sets the numbers for measuring.
//! When it is on, [`INSTRUCTION`] joins the prompt once, so the placeholders need not say how to
//! use the saved copies they name.

use std::ffi::OsString;
use std::path::Path;

use ling_masking::Policy;

use crate::compaction::LIMIT_KEY;

/// The environment variable that switches masking on or off for one process.
pub const ENV: &str = "DREAMFERENCE_MIGHTLING_MASK";

/// The key in the Dreamference config file.
pub const KEY: &str = "mightling_mask_tool_output";

/// The table of numbers in the config file: `high_percent`, `low_percent`, `min_step`,
/// `keep_recent`, `min_chars`.
pub const TABLE: &str = "mightling_mask";

/// Off until the A/B of the spec's §6 shows it costs no resolved task and no time.
pub const DEFAULT: bool = false;

/// The sentence the prompt carries when masking is on (CONTEXT_BUDGET §4.1).
pub const INSTRUCTION: &str = "\n\n# Long outputs moved out of context\n\nTo keep the context small, the output of an older command may be replaced by a line naming the file that holds it in full. Read that file with `sed -n` or `tail` when you need the output again; do not re-run the command for it.\n";

fn settings() -> toml::Table {
    crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok())
        .unwrap_or_default()
}

/// Whether masking is on for this launch.
pub fn enabled_now() -> bool {
    enabled(std::env::var(ENV).ok().as_deref(), &settings())
}

/// The prompt's sentence about saved copies, when masking is on; empty otherwise.
pub fn instruction(on: bool) -> &'static str {
    if on { INSTRUCTION } else { "" }
}

/// Sets the process's masking policy when masking is on, and prunes old segments either way.
pub fn configure(on: bool, args: &[OsString], codex_home: &Path, window: u64) {
    ling_masking::prune(codex_home, std::time::SystemTime::now());
    if !on {
        return;
    }
    let config = std::fs::read_to_string(codex_home.join("config.toml")).unwrap_or_default();
    let limit = effective_limit(args, &config, window);
    ling_masking::set_policy(policy(limit, &settings()), codex_home);
}

/// `DREAMFERENCE_MIGHTLING_MASK`, then `mightling_mask_tool_output`, then [`DEFAULT`].
pub fn enabled(env: Option<&str>, settings: &toml::Table) -> bool {
    if let Some(value) = env.filter(|value| !value.is_empty()) {
        return matches!(value.to_lowercase().as_str(), "1" | "true" | "yes" | "on");
    }
    settings.get(KEY).and_then(toml::Value::as_bool).unwrap_or(DEFAULT)
}

/// The marks for `limit`, with the `[mightling_mask]` table's numbers where it gives them.
pub fn policy(limit: u64, settings: &toml::Table) -> Policy {
    let mut policy = Policy::for_limit(limit);
    let Some(table) = settings.get(TABLE).and_then(toml::Value::as_table) else {
        return policy;
    };
    let number = |key: &str| table.get(key).and_then(toml::Value::as_integer).and_then(|n| u64::try_from(n).ok());
    if let Some(percent) = number("high_percent") {
        policy.high = limit * percent / 100;
    }
    if let Some(percent) = number("low_percent") {
        policy.low = limit * percent / 100;
    }
    if let Some(tokens) = number("min_step") {
        policy.min_step = tokens;
    }
    if let Some(count) = number("keep_recent") {
        policy.keep_recent = count as usize;
    }
    if let Some(chars) = number("min_chars") {
        policy.min_chars = chars as usize;
    }
    policy
}

/// The compaction limit the session will use: the last `-c` of it, then `config.toml`, then the
/// window (the catalog's `auto_compact_token_limit`).
pub fn effective_limit(args: &[OsString], config: &str, window: u64) -> u64 {
    let words: Vec<String> = args.iter().skip(1).map(|arg| arg.to_string_lossy().into_owned()).collect();
    let value_of = |assignment: &str| {
        let (key, value) = assignment.split_once('=')?;
        (key.trim() == LIMIT_KEY).then(|| value.trim().parse::<u64>().ok()).flatten()
    };
    let mut from_args = None;
    for (index, word) in words.iter().enumerate() {
        let assignment = match word.as_str() {
            "-c" | "--config" => words.get(index + 1).map(String::as_str),
            _ => word.strip_prefix("--config=").or_else(|| word.strip_prefix("-c").filter(|rest| !rest.is_empty())),
        };
        if let Some(limit) = assignment.and_then(value_of) {
            from_args = Some(limit);
        }
    }
    from_args
        .or_else(|| config.parse::<toml::Table>().ok()?.get(LIMIT_KEY)?.as_integer().and_then(|n| u64::try_from(n).ok()))
        .unwrap_or(window)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<OsString> {
        words.iter().map(OsString::from).collect()
    }

    fn table(text: &str) -> toml::Table {
        text.parse().unwrap_or_default()
    }

    #[test]
    fn off_unless_switched_on() {
        assert!(!enabled(None, &table("")));
        assert!(enabled(None, &table("mightling_mask_tool_output = true")));
        assert!(enabled(Some("1"), &table("mightling_mask_tool_output = false")));
        assert!(!enabled(Some("off"), &table("mightling_mask_tool_output = true")));
        assert!(!enabled(Some(""), &table("")));
    }

    #[test]
    fn the_limit_is_the_one_the_session_uses() {
        let night = args(&["ling", "-c", "model_auto_compact_token_limit=94144", "exec", "-c", "model_auto_compact_token_limit=44000", "hi"]);
        assert_eq!(effective_limit(&night, "", 262_144), 44_000);
        assert_eq!(effective_limit(&args(&["ling", "--config=model_auto_compact_token_limit=49152"]), "", 262_144), 49_152);
        assert_eq!(effective_limit(&args(&["ling"]), "model_auto_compact_token_limit = 64000\n", 262_144), 64_000);
        assert_eq!(effective_limit(&args(&["ling", "-c", "other=1"]), "", 262_144), 262_144);
    }

    #[test]
    fn the_marks_follow_the_limit_and_the_table() {
        let policy = policy(44_000, &table(""));
        assert_eq!((policy.high, policy.low, policy.min_step), (37_400, 22_000, 16_000));
        // v1's values, the comparison arm.
        let v1 = super::policy(44_000, &table("[mightling_mask]\nhigh_percent = 82\nlow_percent = 55\nmin_step = 8000\n"));
        assert_eq!((v1.high, v1.low, v1.min_step, v1.keep_recent), (36_080, 24_200, 8_000, ling_masking::KEEP_RECENT));
    }

    #[test]
    fn the_prompt_says_how_to_read_a_saved_copy_only_when_masking_is_on() {
        assert!(instruction(true).contains("do not re-run the command"));
        assert!(instruction(false).is_empty());
    }
}
