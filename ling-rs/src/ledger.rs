//! The compaction ledger (specs/DREAMFERENCE_MIGHTLING_COMPACTION.md §10.1).
//!
//! When Codex compacts a session it keeps the user's messages and a model-written summary, and
//! drops every tool call and every tool output. What the summary loses first is the trail: which
//! files were read and changed, which commands failed, what the last test run said. This module
//! rebuilds that trail by rule, from the session's rollout file and `git status`, with no model.
//!
//! `ling ledger` is the command of a `SessionStart` hook with matcher `compact` (the launcher
//! registers it, see `lib.rs`): Codex runs it after each compaction, and what it prints is added
//! to the conversation after the summary, before the next request to the model. It only appends,
//! so it costs no cached prefix.
//!
//! It depends on nothing but the standard library and `serde_json`, so it can be compiled and run
//! outside the Codex workspace.

use std::collections::HashSet;
use std::io::Read;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;

use serde_json::Value;
use serde_json::json;

/// What the ledger says it is. The model has just been handed a summary that reads as if it were
/// the whole record; this line is what tells it otherwise.
pub const FIRST_LINE: &str =
    "Ledger built from the tool history by rule; the tool history itself is no longer in context.";

/// What to do with it: the measured cost of a compaction is commands run again to find what the
/// summary dropped (compaction spec §1), so the ledger says which searches are not needed.
pub const SECOND_LINE: &str =
    "Use it instead of listing or searching the repository again; re-read a file only when you need its exact text.";

const MAX_CHANGED: usize = 40;
const MAX_READ: usize = 40;
const MAX_FAILED: usize = 5;
const MAX_COMMAND_CHARS: usize = 160;
/// Codex spills a hook's context to a file past ~2,500 tokens; the ledger stays well under it.
const MAX_CHARS: usize = 6_000;

/// The trail of one session, as far as rules can rebuild it.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Ledger {
    /// `git status --porcelain` lines (`M path`, `?? path`), or paths named by patches when the
    /// directory is not a repository.
    pub changed: Vec<String>,
    /// Files in the workspace that commands named, most recent first, without the changed ones.
    pub read: Vec<String>,
    /// Exit code and first line of each command that failed since the previous compaction.
    pub failed: Vec<(i64, String)>,
    /// The last test summary line a command printed, and that command's first line.
    pub last_test: Option<(String, String)>,
}

impl Ledger {
    /// Builds the ledger from a rollout's text. `cwd` is the session's working directory;
    /// `git_status` is `git status --porcelain` there, or `None` outside a repository.
    pub fn from_rollout(rollout: &str, cwd: &Path, git_status: Option<&str>) -> Ledger {
        let mut calls: Vec<Call> = Vec::new();
        // Index into `calls` of the first call after each compaction.
        let mut compactions: Vec<usize> = Vec::new();
        for line in rollout.lines() {
            let Ok(record) = serde_json::from_str::<Value>(line) else { continue };
            let payload = &record["payload"];
            match (record["type"].as_str(), payload["type"].as_str()) {
                (Some("compacted"), _) => compactions.push(calls.len()),
                (Some("response_item"), Some("function_call")) => {
                    let arguments: Value = payload["arguments"]
                        .as_str()
                        .and_then(|text| serde_json::from_str(text).ok())
                        .unwrap_or(Value::Null);
                    let command = match &arguments["cmd"] {
                        Value::String(text) => text.clone(),
                        _ => match &arguments["command"] {
                            Value::String(text) => text.clone(),
                            Value::Array(words) => words
                                .iter()
                                .filter_map(Value::as_str)
                                .collect::<Vec<_>>()
                                .join(" "),
                            _ => continue,
                        },
                    };
                    calls.push(Call {
                        id: payload["call_id"].as_str().unwrap_or_default().to_string(),
                        command,
                        workdir: arguments["workdir"].as_str().map(PathBuf::from),
                        output: String::new(),
                    });
                }
                (Some("response_item"), Some("custom_tool_call")) => calls.push(Call {
                    id: payload["call_id"].as_str().unwrap_or_default().to_string(),
                    command: payload["input"].as_str().unwrap_or_default().to_string(),
                    workdir: None,
                    output: String::new(),
                }),
                (Some("response_item"), Some("function_call_output" | "custom_tool_call_output")) => {
                    let id = payload["call_id"].as_str().unwrap_or_default();
                    let output = match &payload["output"] {
                        Value::String(text) => text.clone(),
                        other => other.to_string(),
                    };
                    if let Some(call) = calls.iter_mut().rev().find(|call| call.id == id) {
                        call.output = output;
                    }
                }
                _ => {}
            }
        }

        let changed = match git_status {
            Some(status) => status
                .lines()
                .filter(|line| !line.trim().is_empty())
                .map(|line| line.trim_end().to_string())
                .collect(),
            None => patched_paths(&calls),
        };
        let changed_paths: HashSet<&str> = changed.iter().map(|line| status_path(line)).collect();

        let mut read: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for call in calls.iter().rev() {
            let base = call.workdir.as_deref().unwrap_or(cwd);
            for path in workspace_files(&call.command, base, cwd) {
                if !changed_paths.contains(path.as_str()) && seen.insert(path.clone()) {
                    read.push(path);
                }
            }
        }

        // The hook runs just after a compaction, so the span of interest ends at the last marker
        // and starts at the one before it; with no marker (the command run by hand) it is all.
        let span = match compactions.as_slice() {
            [] => 0..calls.len(),
            [only] => 0..*only,
            [.., previous, last] => *previous..*last,
        };
        let failed = calls[span]
            .iter()
            .filter_map(|call| Some((exit_code(&call.output).filter(|code| *code != 0)?, first_line(&call.command))))
            .collect::<Vec<_>>();
        let failed = failed[failed.len().saturating_sub(MAX_FAILED)..].to_vec();

        let last_test = calls.iter().rev().find_map(|call| {
            let summary = call.output.lines().rev().find(|line| is_test_summary(line))?;
            Some((summary.trim().trim_matches('=').trim().to_string(), first_line(&call.command)))
        });

        Ledger { changed, read, failed, last_test }
    }

    /// The text handed to the model.
    pub fn render(&self) -> String {
        let mut lines = vec![FIRST_LINE.to_string(), SECOND_LINE.to_string()];
        if !self.changed.is_empty() {
            lines.push("Files changed (git status):".to_string());
            push_capped(&mut lines, &self.changed, MAX_CHANGED);
        }
        if !self.read.is_empty() {
            lines.push("Other files the commands read, most recent first:".to_string());
            push_capped(&mut lines, &self.read, MAX_READ);
        }
        if !self.failed.is_empty() {
            lines.push("Commands that failed since the previous compaction:".to_string());
            lines.extend(self.failed.iter().map(|(code, command)| format!("- exit {code}: {command}")));
        }
        if let Some((summary, command)) = &self.last_test {
            lines.push(format!("Last test result, from `{command}`: {summary}"));
        }
        let mut text = lines.join("\n");
        if text.len() > MAX_CHARS {
            let mut end = MAX_CHARS;
            while !text.is_char_boundary(end) {
                end -= 1;
            }
            text.truncate(end);
            text.push_str("\n…");
        }
        text
    }

    /// True when there is nothing to tell: a session that ran no command.
    pub fn is_empty(&self) -> bool {
        self.changed.is_empty() && self.read.is_empty() && self.failed.is_empty() && self.last_test.is_none()
    }
}

struct Call {
    id: String,
    command: String,
    workdir: Option<PathBuf>,
    output: String,
}

fn push_capped(lines: &mut Vec<String>, items: &[String], cap: usize) {
    lines.extend(items.iter().take(cap).map(|item| format!("- {item}")));
    if items.len() > cap {
        lines.push(format!("- … and {} more", items.len() - cap));
    }
}

/// The path of a `git status --porcelain` line (`XY path`, or `XY old -> new` for a rename).
fn status_path(line: &str) -> &str {
    let path = line.get(3..).unwrap_or(line).trim();
    path.rsplit(" -> ").next().unwrap_or(path).trim_matches('"')
}

/// Paths named by `apply_patch` headers: what stands in for `git status` outside a repository.
fn patched_paths(calls: &[Call]) -> Vec<String> {
    let mut paths: Vec<String> = Vec::new();
    for call in calls {
        for line in call.command.lines() {
            for marker in ["*** Add File: ", "*** Update File: ", "*** Delete File: "] {
                if let Some(path) = line.trim_start().strip_prefix(marker) {
                    let path = path.trim().to_string();
                    if !paths.contains(&path) {
                        paths.push(path);
                    }
                }
            }
        }
    }
    paths
}

/// The files inside the workspace that a command names: every word of it that is an existing
/// file under `cwd`, relative to `cwd`. Checking the disk rather than the spelling keeps
/// interpreters, options and words that merely look like paths out of the list.
fn workspace_files(command: &str, base: &Path, cwd: &Path) -> Vec<String> {
    let root = cwd.canonicalize().unwrap_or_else(|_| cwd.to_path_buf());
    let mut files: Vec<String> = Vec::new();
    for word in command_words(command) {
        let word = word.trim_end_matches('.');
        if word.len() < 3 || word.len() > 300 || !(word.contains('/') || word.contains('\\') || word.contains('.')) {
            continue;
        }
        let candidate = if Path::new(word).is_absolute() { PathBuf::from(word) } else { base.join(word) };
        let Ok(resolved) = candidate.canonicalize() else { continue };
        if !resolved.is_file() {
            continue;
        }
        let Ok(relative) = resolved.strip_prefix(&root) else { continue };
        // Written with `/`, as git names files, so a file `git status` lists is recognised.
        let relative = relative
            .components()
            .map(|part| part.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
        if !relative.starts_with(".git/") && !files.contains(&relative) {
            files.push(relative);
        }
    }
    files
}

/// The words of a command that could name a file: split at whitespace, shell punctuation, `=`,
/// `,` and `:` (`file.py:12`), except that a drive path (`C:\x`, `C:/x`) keeps its colon.
fn command_words(command: &str) -> Vec<&str> {
    let separators = |c: char| c.is_whitespace() || ";|&<>()\"'`=,".contains(c);
    let mut words = Vec::new();
    for word in command.split(separators) {
        let bytes = word.as_bytes();
        let drive = bytes.len() > 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' && (bytes[2] == b'\\' || bytes[2] == b'/');
        if drive {
            let (head, rest) = word.split_at(2);
            let mut parts = rest.split(':');
            if let Some(first) = parts.next() {
                words.push(&word[..head.len() + first.len()]);
            }
            words.extend(parts);
        } else {
            words.extend(word.split(':'));
        }
    }
    words
}

/// The exit code Codex's command tool reports in its output (`Process exited with code N`).
fn exit_code(output: &str) -> Option<i64> {
    const MARKER: &str = "Process exited with code ";
    let rest = &output[output.find(MARKER)? + MARKER.len()..];
    let digits: String = rest.chars().take_while(|c| c.is_ascii_digit() || *c == '-').collect();
    digits.parse().ok()
}

/// Recognises the closing line of pytest, `cargo test`, Jest and `go test`.
fn is_test_summary(line: &str) -> bool {
    let line = line.trim();
    let counted = |word: &str| {
        line.split(word).next().is_some_and(|before| {
            line.contains(word) && before.trim_end().chars().last().is_some_and(|c| c.is_ascii_digit())
        })
    };
    (line.contains(" in ") && (counted(" passed") || counted(" failed") || counted(" error")))
        || line.starts_with("test result: ")
        || line.starts_with("Tests: ")
        || (line.starts_with("ok  \t") || line.starts_with("FAIL\t"))
}

fn first_line(command: &str) -> String {
    let line = command.lines().next().unwrap_or_default().trim();
    if line.chars().count() > MAX_COMMAND_CHARS {
        format!("{}…", line.chars().take(MAX_COMMAND_CHARS - 1).collect::<String>())
    } else {
        line.to_string()
    }
}

/// `git status --porcelain` in `cwd`, or `None` when it is not a repository or git is missing.
pub fn git_status(cwd: &Path) -> Option<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(cwd)
        .args(["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    output.status.success().then(|| String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Answers the hook: `input` is the JSON Codex writes to a `SessionStart` hook's stdin. Returns
/// the JSON to print, or `None` when there is nothing to add (the session start was not a
/// compaction, the rollout cannot be read, or no command has run).
pub fn hook(input: &str) -> Option<String> {
    let input: Value = serde_json::from_str(input).ok()?;
    if input["source"].as_str().is_some_and(|source| source != "compact") {
        return None;
    }
    let rollout = std::fs::read_to_string(input["transcript_path"].as_str()?).ok()?;
    let cwd = PathBuf::from(input["cwd"].as_str()?);
    let ledger = Ledger::from_rollout(&rollout, &cwd, git_status(&cwd).as_deref());
    if ledger.is_empty() {
        return None;
    }
    Some(
        json!({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ledger.render()}})
            .to_string(),
    )
}

/// `ling ledger`: the hook itself (JSON on stdin, JSON on stdout), or `ling ledger show
/// <rollout> [<cwd>]`, which prints the ledger of a recorded session for a person to read.
pub fn run_cli(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        None => {
            let mut input = String::new();
            if std::io::stdin().read_to_string(&mut input).is_err() {
                return 0;
            }
            if let Some(answer) = hook(&input) {
                println!("{answer}");
            }
            // Never an error: a failing SessionStart hook would be reported in the session.
            0
        }
        Some("show") => {
            let Some(path) = args.get(1) else {
                eprintln!("Usage: ling ledger show <rollout.jsonl> [<cwd>]");
                return 2;
            };
            let Ok(rollout) = std::fs::read_to_string(path) else {
                eprintln!("ling ledger: cannot read {path}");
                return 1;
            };
            let cwd = match args.get(2) {
                Some(cwd) => PathBuf::from(cwd),
                None => std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
            };
            println!("{}", Ledger::from_rollout(&rollout, &cwd, git_status(&cwd).as_deref()).render());
            0
        }
        Some(_) => {
            eprintln!("Usage: ling ledger (a SessionStart hook: JSON on stdin) | ling ledger show <rollout.jsonl> [<cwd>]");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mightling-ledger-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("src")).unwrap_or_default();
        dir.canonicalize().unwrap_or(dir)
    }

    fn call(id: &str, cmd: &str) -> String {
        json!({"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
            "call_id": id, "arguments": json!({"cmd": cmd}).to_string()}})
        .to_string()
    }

    fn output(id: &str, code: i64, text: &str) -> String {
        json!({"type": "response_item", "payload": {"type": "function_call_output", "call_id": id,
            "output": format!("Chunk ID: 1\nWall time: 0.1 seconds\nProcess exited with code {code}\nOutput:\n{text}")}})
        .to_string()
    }

    const COMPACTED: &str = r#"{"type":"compacted","payload":{"message":"summary"}}"#;

    #[test]
    fn a_drive_path_keeps_its_colon_and_a_line_number_does_not() {
        assert_eq!(command_words(r"cat C:\x\a.py:12 b.py:3"), vec!["cat", r"C:\x\a.py", "12", "b.py", "3"]);
        assert_eq!(command_words("sed -n 1,5p C:/x/b.py"), vec!["sed", "-n", "1", "5p", "C:/x/b.py"]);
        assert_eq!(command_words("PYTHONPATH=src:lib x"), vec!["PYTHONPATH", "src", "lib", "x"]);
    }

    #[test]
    fn the_trail_is_rebuilt_from_the_rollout() {
        let dir = scratch("trail");
        for name in ["src/a.py", "src/b.py", "README.md"] {
            std::fs::write(dir.join(name), "x").unwrap_or_default();
        }
        let absolute = dir.join("src/b.py");
        let rollout = [
            call("1", "cat src/a.py README.md"),
            output("1", 0, "x"),
            call("2", &format!("sed -n 1,5p {}; cat missing.py", absolute.display())),
            output("2", 1, "cat: missing.py: No such file"),
            call("3", "/usr/bin/python3 -m pytest tests -q"),
            output("3", 1, "....F\n=========== 1 failed, 4 passed in 1.43s ==========="),
            COMPACTED.to_string(),
        ]
        .join("\n");
        let ledger = Ledger::from_rollout(&rollout, &dir, Some(" M src/a.py\n?? new.txt\n"));
        assert_eq!(ledger.changed, vec![" M src/a.py", "?? new.txt"]);
        // Most recent first, the changed file left out, the interpreter and the missing file too.
        assert_eq!(ledger.read, vec!["src/b.py", "README.md"]);
        assert_eq!(ledger.failed.iter().map(|(code, _)| *code).collect::<Vec<_>>(), vec![1, 1]);
        assert_eq!(
            ledger.last_test,
            Some(("1 failed, 4 passed in 1.43s".to_string(), "/usr/bin/python3 -m pytest tests -q".to_string()))
        );
        let text = ledger.render();
        assert!(text.starts_with(FIRST_LINE));
        assert!(text.contains("- ?? new.txt") && text.contains("- src/b.py"));
        assert!(text.contains("Last test result, from `/usr/bin/python3 -m pytest tests -q`: 1 failed, 4 passed in 1.43s"));
    }

    #[test]
    fn failures_are_those_since_the_previous_compaction() {
        let dir = scratch("span");
        let rollout = [
            call("1", "false"),
            output("1", 3, ""),
            COMPACTED.to_string(),
            call("2", "ls nowhere"),
            output("2", 2, ""),
            call("3", "true"),
            output("3", 0, ""),
            COMPACTED.to_string(),
        ]
        .join("\n");
        let ledger = Ledger::from_rollout(&rollout, &dir, Some(""));
        assert_eq!(ledger.failed, vec![(2, "ls nowhere".to_string())]);
    }

    #[test]
    fn without_git_the_changed_files_come_from_the_patches() {
        let dir = scratch("nogit");
        let patch = json!({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
            "call_id": "1", "input": "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** Add File: notes.md\n+z\n*** End Patch"}})
        .to_string();
        let ledger = Ledger::from_rollout(&patch, &dir, None);
        assert_eq!(ledger.changed, vec!["src/a.py", "notes.md"]);
    }

    #[test]
    fn files_outside_the_workspace_are_not_listed() {
        let dir = scratch("outside");
        let other = scratch("elsewhere");
        std::fs::write(other.join("secret.txt"), "x").unwrap_or_default();
        std::fs::write(dir.join("in.txt"), "x").unwrap_or_default();
        let command = format!("cat {} in.txt ../{}/secret.txt", other.join("secret.txt").display(),
            other.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default());
        assert_eq!(workspace_files(&command, &dir, &dir), vec!["in.txt"]);
    }

    #[test]
    fn test_summaries_of_four_runners_are_recognised() {
        assert!(is_test_summary("=========== 1 failed, 4 passed in 1.43s ==========="));
        assert!(is_test_summary("39 passed in 13.63s"));
        assert!(is_test_summary("test result: ok. 59 passed; 0 failed; 0 ignored"));
        assert!(is_test_summary("Tests:       2 failed, 10 passed, 12 total"));
        assert!(is_test_summary("ok  \tgithub.com/x/y\t0.012s"));
        assert!(!is_test_summary("the check passed in review"));
        assert!(!is_test_summary("def test_passed():"));
    }

    #[test]
    fn the_hook_answers_only_a_compaction_and_only_with_something_to_say() {
        let dir = scratch("hook");
        std::fs::write(dir.join("a.txt"), "x").unwrap_or_default();
        let rollout = dir.join("rollout.jsonl");
        std::fs::write(&rollout, [call("1", "cat a.txt"), output("1", 0, "x"), COMPACTED.to_string()].join("\n"))
            .unwrap_or_default();
        let input = |source: &str| {
            json!({"source": source, "transcript_path": rollout, "cwd": dir, "hook_event_name": "SessionStart"})
                .to_string()
        };
        assert_eq!(hook(&input("startup")), None);
        let answer: Value = serde_json::from_str(&hook(&input("compact")).unwrap_or_default()).unwrap_or_default();
        assert_eq!(answer["hookSpecificOutput"]["hookEventName"], "SessionStart");
        let context = answer["hookSpecificOutput"]["additionalContext"].as_str().unwrap_or_default();
        assert!(context.starts_with(FIRST_LINE) && context.contains("- a.txt"), "{context}");
        // A session that ran nothing has no ledger.
        std::fs::write(&rollout, COMPACTED).unwrap_or_default();
        assert_eq!(hook(&input("compact")), None);
        assert_eq!(hook("not json"), None);
    }

    #[test]
    fn a_long_ledger_is_cut_below_the_spill_threshold() {
        let ledger = Ledger {
            changed: (0..200).map(|index| format!(" M {}", "x".repeat(100) + &index.to_string())).collect(),
            ..Ledger::default()
        };
        let text = ledger.render();
        assert!(text.len() <= MAX_CHARS + 8 && text.contains("… and 160 more"), "{}", text.len());
    }
}
