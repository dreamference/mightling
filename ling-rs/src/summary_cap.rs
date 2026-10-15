//! A length bound on the compaction summary, through Codex's own `compact_prompt` setting.
//!
//! The summary is the half of a compaction the prefix cache cannot save: 925–2,588 tokens at
//! 25–41 tok/s on 2026-10-13, the drafter accepting 3–5 of 16 tokens on prose
//! (`docs/dev/turn-end-compaction/2026-10-13.md`). Codex's prompt (426 bytes) asks for a concise
//! summary but sets no length. When [`WORDS_ENV`] names a number of words, the launcher passes
//! Codex's own prompt, byte for byte, plus one paragraph bounding the length, as
//! `-c compact_prompt=…`. It also asks for the summary as the reply and no tool call: since patch
//! 0027 the compaction request offers the turn's tools (ling-tools' `compaction`).
//!
//! On by default at [`DEFAULT_WORDS`] since the 2026-10-14 bench (spec §14.3): every arm ran with
//! it, and turn-end compactions took a median 53.5 s against 102 s the day before (the two over
//! 115 s were summaries of 3,264 and 4,165 tokens: the bound is an instruction, not a hard limit).
//! `DREAMFERENCE_MIGHTLING_COMPACT_SUMMARY_WORDS=0` switches it off; another number moves it. A
//! `compact_prompt` or `experimental_compact_prompt_file` the user set, on the command line or in
//! `config.toml`, is left alone.

use std::ffi::OsString;
use std::path::Path;

use crate::compaction;

/// The summary's length bound in words; unset or empty is [`DEFAULT_WORDS`], 0 is off.
pub const WORDS_ENV: &str = "DREAMFERENCE_MIGHTLING_COMPACT_SUMMARY_WORDS";

/// About 1,000 tokens, ~35 s at the measured 25–41 tok/s (the owner's value for the v2 bench).
pub const DEFAULT_WORDS: u32 = 800;

/// Codex's compaction prompt key (`config/src/config_toml.rs`).
pub const PROMPT_KEY: &str = "compact_prompt";

/// The file form of the same setting; either one set by the user wins.
pub const PROMPT_FILE_KEY: &str = "experimental_compact_prompt_file";

/// For this launch: reads [`WORDS_ENV`] and `config.toml`, then [`with_summary_cap`].
pub fn prepare(args: Vec<OsString>, codex_home: &Path) -> Vec<OsString> {
    let words = words(std::env::var(WORDS_ENV).ok().as_deref());
    let config = std::fs::read_to_string(codex_home.join("config.toml")).unwrap_or_default();
    with_summary_cap(args, &config, words)
}

/// The bound [`WORDS_ENV`] asks for: [`DEFAULT_WORDS`] when it is unset, empty or not a number,
/// `None` (off) for 0.
pub fn words(env: Option<&str>) -> Option<u32> {
    match env.map(str::trim).filter(|value| !value.is_empty()) {
        None => Some(DEFAULT_WORDS),
        Some(value) => match value.parse::<u32>() {
            Ok(0) => None,
            Ok(words) => Some(words),
            Err(_) => Some(DEFAULT_WORDS),
        },
    }
}

/// Codex's compaction prompt with the length bound appended.
pub fn prompt(words: u32) -> String {
    format!(
        "{}\n\nKeep the summary under {words} words. Write it as your reply; do not call any tool.\n",
        codex_prompts::SUMMARIZATION_PROMPT.trim_end()
    )
}

/// `-c compact_prompt="<prompt(words)>"` after the program name, as a TOML string (Codex parses a
/// `-c` value as TOML), unless `words` is `None` or the user set the prompt.
pub fn with_summary_cap(args: Vec<OsString>, config: &str, words: Option<u32>) -> Vec<OsString> {
    let Some(words) = words else { return args };
    if compaction::is_set(&args, config, PROMPT_KEY) || compaction::is_set(&args, config, PROMPT_FILE_KEY) {
        return args;
    }
    compaction::with_assignment(args, PROMPT_KEY, toml::Value::String(prompt(words)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<OsString> {
        words.iter().map(OsString::from).collect()
    }

    /// What Codex does with a `-c key=value` (`utils/cli/src/config_override.rs`): TOML first.
    fn parsed(assignment: &str) -> (String, toml::Value) {
        let (key, raw) = assignment.split_once('=').unwrap_or((assignment, ""));
        let table: toml::Table = format!("_x_ = {raw}").parse().unwrap_or_default();
        (key.to_string(), table.get("_x_").cloned().unwrap_or(toml::Value::Boolean(false)))
    }

    #[test]
    fn on_at_800_words_unless_switched_off_with_0() {
        assert_eq!(words(None), Some(DEFAULT_WORDS));
        assert_eq!(words(Some("")), Some(DEFAULT_WORDS));
        assert_eq!(words(Some("0")), None);
        assert_eq!(words(Some("many")), Some(DEFAULT_WORDS));
        assert_eq!(words(Some(" 500 ")), Some(500));
        let given = args(&["ling", "resume"]);
        assert_eq!(with_summary_cap(given.clone(), "", None), given);
    }

    #[test]
    fn the_prompt_is_codexs_own_plus_the_bound() {
        let text = prompt(800);
        assert!(text.starts_with(codex_prompts::SUMMARIZATION_PROMPT.trim_end()));
        // The bench and the nightly audit find a compaction request by this phrase.
        assert!(text.contains("CONTEXT CHECKPOINT COMPACTION"));
        assert!(text.contains("under 800 words"));
        assert!(text.contains("do not call any tool"));
    }

    #[test]
    fn the_prompt_reaches_codex_as_one_toml_string() {
        let out = with_summary_cap(args(&["ling", "resume", "--last"]), "", Some(800));
        assert_eq!(out[1], "-c");
        assert_eq!(&out[3..], &args(&["resume", "--last"])[..]);
        let (key, value) = parsed(&out[2].to_string_lossy());
        assert_eq!(key, PROMPT_KEY);
        assert_eq!(value.as_str(), Some(prompt(800).as_str()));
    }

    #[test]
    fn a_prompt_the_user_set_wins() {
        let given = args(&["ling", "-c", "compact_prompt=Summarise."]);
        assert_eq!(with_summary_cap(given.clone(), "", Some(800)), given);
        let plain = args(&["ling"]);
        assert_eq!(with_summary_cap(plain.clone(), "compact_prompt = \"Mine\"\n", Some(800)), plain);
        assert_eq!(with_summary_cap(plain.clone(), "experimental_compact_prompt_file = \"/p.md\"\n", Some(800)), plain);
    }
}
