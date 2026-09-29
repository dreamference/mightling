//! Codex's help text, rebranded as it is parsed.
//!
//! Codex's subcommands and options describe themselves in doc comments — "Run Codex
//! non-interactively", "Manage Codex plugins" — dozens of them across several crates. Patching each
//! string would make the patch series grow with every Codex release, so patch 0002 instead has
//! `cli_main` parse through [`parse`], which walks the clap command tree once and rewrites the
//! prose before clap renders any help or error.
//!
//! Only the product name changes. Paths and identifiers that merely contain it — `~/.codex`,
//! `$CODEX_HOME`, `codex-code-mode-host`, `openai/codex` — are left alone, because they are real
//! and renaming them in help would describe files that do not exist.

use std::ffi::OsString;

use clap::Command;
use clap::Parser;

/// Parses `args` into `T` exactly as `T::parse_from` would, but with rebranded help text.
pub fn parse<T: Parser>(args: Vec<OsString>) -> T {
    let mut command = rebrand_command(T::command());
    let mut matches = command.clone().get_matches_from(args);
    T::from_arg_matches_mut(&mut matches).unwrap_or_else(|err| err.format(&mut command).exit())
}

/// Rewrites every piece of prose on a command, its arguments and, recursively, its subcommands.
pub fn rebrand_command(mut command: Command) -> Command {
    if let Some(text) = command.get_about().map(|s| rebrand_text(&s.to_string())) {
        command = command.about(text);
    }
    if let Some(text) = command.get_long_about().map(|s| rebrand_text(&s.to_string())) {
        command = command.long_about(text);
    }
    if let Some(text) = command.get_before_help().map(|s| rebrand_text(&s.to_string())) {
        command = command.before_help(text);
    }
    if let Some(text) = command.get_before_long_help().map(|s| rebrand_text(&s.to_string())) {
        command = command.before_long_help(text);
    }
    if let Some(text) = command.get_after_help().map(|s| rebrand_text(&s.to_string())) {
        command = command.after_help(text);
    }
    if let Some(text) = command.get_after_long_help().map(|s| rebrand_text(&s.to_string())) {
        command = command.after_long_help(text);
    }
    if let Some(name) = command.get_bin_name().map(rebrand_text) {
        command = command.bin_name(name);
    }
    if let Some(name) = command.get_display_name().map(rebrand_text) {
        command = command.display_name(name);
    }
    command
        .mut_args(|mut arg| {
            if let Some(text) = arg.get_help().map(|s| rebrand_text(&s.to_string())) {
                arg = arg.help(text);
            }
            if let Some(text) = arg.get_long_help().map(|s| rebrand_text(&s.to_string())) {
                arg = arg.long_help(text);
            }
            arg
        })
        .mut_subcommands(rebrand_command)
}

/// Replaces the product name in a piece of help text.
///
/// "OpenAI Codex" and "Codex CLI" become "Puffin"; any other capitalised "Codex" as a word becomes
/// "Puffin"; a lowercase `codex` becomes `puffin` only where it is the command a user types —
/// a whole word not attached to a path, a variable or a longer identifier.
pub fn rebrand_text(text: &str) -> String {
    let text = text.replace("OpenAI Codex", "Puffin").replace("Codex CLI", "Puffin");
    let text = replace_word(&text, "Codex", "Puffin", |_, _| true);
    replace_word(&text, "codex", "puffin", |before, after| {
        !matches!(before, Some('.' | '/' | '~' | '$' | '-' | '_' | '@'))
            && !matches!(after, Some('-' | '_' | '/' | '.' | '@'))
    })
}

/// Replaces `word` where it stands alone (not inside a longer alphanumeric run) and `allowed`
/// accepts the characters on either side.
fn replace_word(
    text: &str,
    word: &str,
    replacement: &str,
    allowed: impl Fn(Option<char>, Option<char>) -> bool,
) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    let mut before: Option<char> = None;
    while let Some(index) = rest.find(word) {
        let (head, tail) = rest.split_at(index);
        out.push_str(head);
        let prev = head.chars().next_back().or(before);
        let next = tail[word.len()..].chars().next();
        let standalone = !prev.is_some_and(char::is_alphanumeric)
            && !next.is_some_and(char::is_alphanumeric);
        if standalone && allowed(prev, next) {
            out.push_str(replacement);
        } else {
            out.push_str(word);
        }
        before = word.chars().next_back();
        rest = &tail[word.len()..];
    }
    out.push_str(rest);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_product_name_is_replaced_in_prose() {
        assert_eq!(rebrand_text("Codex CLI"), "Puffin");
        assert_eq!(rebrand_text("Run Codex non-interactively"), "Run Puffin non-interactively");
        assert_eq!(rebrand_text("a Codex-provided sandbox"), "a Puffin-provided sandbox");
        assert_eq!(rebrand_text("OpenAI Codex (v1)"), "Puffin (v1)");
    }

    #[test]
    fn the_typed_command_is_replaced_but_paths_and_identifiers_are_not() {
        assert_eq!(rebrand_text("codex exec [OPTIONS]"), "puffin exec [OPTIONS]");
        assert_eq!(rebrand_text("  codex plugin marketplace add ./x"), "  puffin plugin marketplace add ./x");
        assert_eq!(rebrand_text("run `codex` again"), "run `puffin` again");
        assert_eq!(rebrand_text("from `~/.codex/config.toml`"), "from `~/.codex/config.toml`");
        assert_eq!(rebrand_text("$CODEX_HOME/x"), "$CODEX_HOME/x");
        assert_eq!(rebrand_text("codex-code-mode-host"), "codex-code-mode-host");
        assert_eq!(rebrand_text("openai/codex releases"), "openai/codex releases");
        assert_eq!(rebrand_text("codexes"), "codexes");
    }

    #[test]
    fn help_is_rebranded_through_the_whole_command_tree() {
        let command = rebrand_command(
            Command::new("codex")
                .about("Codex CLI")
                .arg(clap::Arg::new("x").long("x").help("Used by Codex"))
                .subcommand(Command::new("exec").about("Run Codex non-interactively")),
        );
        assert_eq!(command.get_about().map(ToString::to_string), Some("Puffin".into()));
        let arg_help = command
            .get_arguments()
            .find(|arg| arg.get_id() == "x")
            .and_then(|arg| arg.get_help())
            .map(ToString::to_string);
        assert_eq!(arg_help, Some("Used by Puffin".into()));
        let exec_about = command
            .find_subcommand("exec")
            .and_then(|exec| exec.get_about())
            .map(ToString::to_string);
        assert_eq!(exec_about, Some("Run Puffin non-interactively".into()));
    }
}
