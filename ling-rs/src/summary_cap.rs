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
//! Off unless the variable is set: for now only the turn-end compaction bench sets it, in all
//! of its arms (`scripts/turn_end_compaction_bench.py`). A `compact_prompt` or
//! `experimental_compact_prompt_file` the user set, on the command line or in `config.toml`, is
//! left alone.

use std::ffi::OsString;
use std::path::Path;

use crate::compaction;

/// The summary's length bound in words; unset, empty or 0 is off.
pub const WORDS_ENV: &str = "DREAMFERENCE_MIGHTLING_COMPACT_SUMMARY_WORDS";

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

/// The bound [`WORDS_ENV`] asks for, if any.
pub fn words(env: Option<&str>) -> Option<u32> {
    env.and_then(|value| value.trim().parse::<u32>().ok()).filter(|words| *words > 0)
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
    fn off_unless_a_number_of_words_is_given() {
        assert_eq!(words(None), None);
        assert_eq!(words(Some("")), None);
        assert_eq!(words(Some("0")), None);
        assert_eq!(words(Some("many")), None);
        assert_eq!(words(Some(" 800 ")), Some(800));
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
