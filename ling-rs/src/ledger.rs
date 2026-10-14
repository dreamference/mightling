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
//! Two more things since SWE-bench night 6 (2026-10-14, `docs/dev/code-mode-night6.md`): cells
//! of Code Mode's `exec` failed 12% of the time right after a compaction against 7% elsewhere,
//! because the summary drops the examples of working cells, so the ledger restates one working
//! cell from the session; and 6 of 60 summaries were only a tool call, so when the last summary is
//! degenerate the ledger carries the previous one. Both are left out when
//! [`CODE_MODE_FIXES_ENV`] says `off` (the benchmark arm that reproduces night 6).
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
/// A restored summary shorter than this is not worth its line.
const MIN_RESTORED_CHARS: usize = 200;
/// A summary body under this many characters is degenerate (night 6's six were 120 to 308).
const MIN_SUMMARY_CHARS: usize = 400;
/// The longest working cell restated as the example; a longer one is not an example.
const MAX_EXAMPLE_CHARS: usize = 240;

/// The same variable `ling-rs/tools` reads (`ling_tools::custom::FIXES_ENV`; the hook is its own
/// process, so it reads the environment itself): `off` leaves the Code Mode lines out.
pub const CODE_MODE_FIXES_ENV: &str = "DREAMFERENCE_MIGHTLING_CODE_MODE_FIXES";

/// Codex's summary message opens with a fixed preamble ending in this; the summary is what
/// follows. A message without it is taken whole.
const SUMMARY_PREAMBLE_END: &str = "assist with your own analysis:\n";

/// The cell restated when the session used `exec` but none of its working cells is short enough
/// to serve: the form 92% of night 6's cells took.
pub const CANONICAL_EXAMPLE: &str =
    "const r = await tools.exec_command({cmd: \"cd /testbed && git status --short\"});\ntext(r.output);";

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
    /// A working `exec` cell of this session, when it used Code Mode's `exec`.
    pub exec_example: Option<String>,
    /// The previous summary, when the last one was degenerate (only a tool call, or too short).
    pub restored_summary: Option<String>,
}

impl Ledger {
    /// Builds the ledger from a rollout's text. `cwd` is the session's working directory;
    /// `git_status` is `git status --porcelain` there, or `None` outside a repository.
    pub fn from_rollout(rollout: &str, cwd: &Path, git_status: Option<&str>) -> Ledger {
        let mut calls: Vec<Call> = Vec::new();
        // Index into `calls` of the first call after each compaction.
        let mut compactions: Vec<usize> = Vec::new();
        // Each compaction's summary, without Codex's preamble.
        let mut summaries: Vec<String> = Vec::new();
        for line in rollout.lines() {
            let Ok(record) = serde_json::from_str::<Value>(line) else { continue };
            let payload = &record["payload"];
            match (record["type"].as_str(), payload["type"].as_str()) {
                (Some("compacted"), _) => {
                    compactions.push(calls.len());
                    summaries.push(summary_body(payload["message"].as_str().unwrap_or_default()).to_string());
                }
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
                        name: payload["name"].as_str().unwrap_or_default().to_string(),
                        command,
                        workdir: arguments["workdir"].as_str().map(PathBuf::from),
                        output: String::new(),
                    });
                }
                (Some("response_item"), Some("custom_tool_call")) => calls.push(Call {
                    id: payload["call_id"].as_str().unwrap_or_default().to_string(),
                    name: payload["name"].as_str().unwrap_or_default().to_string(),
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

        // The last working cell that nested `exec_command` and is short enough to read as an
        // example; the canonical one when the session used `exec` but no such cell is.
        let cells: Vec<&Call> = calls.iter().filter(|call| call.name == "exec").collect();
        let exec_example = cells
            .iter()
            .rev()
            .find(|cell| {
                cell.output.starts_with("Script completed")
                    && cell.command.contains("tools.exec_command")
                    && cell.command.contains("text(")
                    && cell.command.trim().chars().count() <= MAX_EXAMPLE_CHARS
            })
            .map(|cell| cell.command.trim().to_string())
            .or_else(|| (!cells.is_empty()).then(|| CANONICAL_EXAMPLE.to_string()));

        // The hook runs just after a compaction: when its summary is degenerate, the nearest
        // earlier summary that is not.
        let restored_summary = match summaries.last() {
            Some(last) if is_degenerate_summary(last) => summaries
                .iter()
                .rev()
                .skip(1)
                .find(|summary| !is_degenerate_summary(summary))
                .map(|summary| summary.trim().to_string()),
            _ => None,
        };

        Ledger { changed, read, failed, last_test, exec_example, restored_summary }
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
        if let Some(example) = &self.exec_example {
            lines.push("A Code Mode `exec` cell that worked in this session (nested tools are on `tools`; for one shell command, call `exec_command` directly):".to_string());
            lines.push(example.clone());
        }
        let mut text = lines.join("\n");
        truncate_chars(&mut text, MAX_CHARS);
        // The restored summary takes what is left of the budget, never more.
        if let Some(summary) = &self.restored_summary {
            let mut restored = summary.clone();
            let budget = MAX_CHARS.saturating_sub(text.len() + RESTORED_LINE.len() + 2);
            if budget >= MIN_RESTORED_CHARS {
                truncate_chars(&mut restored, budget);
                text.push_str("\n\n");
                text.push_str(RESTORED_LINE);
                text.push('\n');
                text.push_str(&restored);
            }
        }
        text
    }

    /// True when there is nothing to tell: a session that ran no command.
    pub fn is_empty(&self) -> bool {
        self.changed.is_empty()
            && self.read.is_empty()
            && self.failed.is_empty()
            && self.last_test.is_none()
            && self.exec_example.is_none()
            && self.restored_summary.is_none()
    }

    /// The ledger as it was before night 6's fixes: without the Code Mode lines.
    pub fn without_code_mode_fixes(mut self) -> Ledger {
        self.exec_example = None;
        self.restored_summary = None;
        self
    }
}

/// What introduces a restored summary.
const RESTORED_LINE: &str =
    "The last compaction summary was empty or only a tool call; the previous summary follows, older than the lines above:";

/// Cuts `text` to at most `max` bytes at a character boundary, marking the cut.
fn truncate_chars(text: &mut String, max: usize) {
    if text.len() > max {
        let mut end = max;
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text.truncate(end);
        text.push_str("\n…");
    }
}

/// The summary in a `compacted` record's message: what follows Codex's preamble.
fn summary_body(message: &str) -> &str {
    match message.find(SUMMARY_PREAMBLE_END) {
        Some(start) => &message[start + SUMMARY_PREAMBLE_END.len()..],
        None => message,
    }
}

/// A summary that is only a tool call (the model answered the compaction request with a call,
/// at most a line of prose before it: night 6's sympy-18698 wrote "Let me create a comprehensive
/// handoff summary" and then called `exec`), or too short to have kept anything.
fn is_degenerate_summary(summary: &str) -> bool {
    let prose = summary.split("<tool_call>").next().unwrap_or_default();
    let prose = prose.split("<function").next().unwrap_or_default().trim();
    prose.chars().count() < MIN_SUMMARY_CHARS
}

struct Call {
    id: String,
    name: String,
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

/// Whether the Code Mode lines are on, from [`CODE_MODE_FIXES_ENV`]'s value: only `off` turns
/// them off (the same reading as `ling_tools::custom::fixes_on_from`).
pub fn code_mode_fixes_on(setting: Option<&str>) -> bool {
    !setting.is_some_and(|value| value.trim().eq_ignore_ascii_case("off"))
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
    let mut ledger = Ledger::from_rollout(&rollout, &cwd, git_status(&cwd).as_deref());
    if !code_mode_fixes_on(std::env::var(CODE_MODE_FIXES_ENV).ok().as_deref()) {
        ledger = ledger.without_code_mode_fixes();
    }
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

    /// A Code Mode cell and its output, as Codex records a translated `exec` call.
    fn cell(id: &str, input: &str, status: &str) -> String {
        [
            json!({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": id, "input": input}}).to_string(),
            json!({"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": id,
                "output": format!("{status}\nWall time 0.0 seconds\nOutput:\n")}}).to_string(),
        ]
        .join("\n")
    }

    /// A `compacted` record whose summary is `body`, behind Codex's preamble.
    fn compacted(body: &str) -> String {
        let message = format!("Another language model started to solve this problem and produced a summary. Here is the summary, use the information in this summary to {SUMMARY_PREAMBLE_END}{body}");
        json!({"type": "compacted", "payload": {"message": message}}).to_string()
    }

    #[test]
    fn a_working_exec_cell_is_restated_and_a_session_without_exec_gets_none() {
        let dir = scratch("cell");
        let long = format!("const r = await tools.exec_command({{cmd: \"{}\"}});\ntext(r.output);", "x".repeat(300));
        let rollout = [
            cell("1", "const r = await exec_command({cmd: 'ls'});", "Script failed"),
            cell("2", "const r = await tools.exec_command({cmd: \"cd /testbed && git diff --stat\"});\ntext(r.output);\n", "Script completed"),
            cell("3", &long, "Script completed"),
            cell("4", "text('plan')", "Script completed"),
            cell("5", "await tools.exec_command({cmd: 'git diff'})", "Script completed"),
            COMPACTED.to_string(),
        ]
        .join("\n");
        let ledger = Ledger::from_rollout(&rollout, &dir, Some(""));
        // The last working cell that nests `exec_command`, prints its output and is short
        // enough: not the failed one, not the long one, not the one that calls no tool, not the
        // one that prints nothing.
        assert_eq!(
            ledger.exec_example.as_deref(),
            Some("const r = await tools.exec_command({cmd: \"cd /testbed && git diff --stat\"});\ntext(r.output);")
        );
        let text = ledger.render();
        assert!(text.contains("A Code Mode `exec` cell that worked in this session") && text.contains("git diff --stat"), "{text}");
        // Only failed cells: the canonical example stands in.
        let only_failed = [cell("1", "exec_command({cmd: 'ls'})", "Script failed"), COMPACTED.to_string()].join("\n");
        assert_eq!(Ledger::from_rollout(&only_failed, &dir, Some("")).exec_example.as_deref(), Some(CANONICAL_EXAMPLE));
        // A session that never used `exec` says nothing about it.
        let plain = [call("1", "ls"), output("1", 0, "a"), COMPACTED.to_string()].join("\n");
        let ledger = Ledger::from_rollout(&plain, &dir, Some(""));
        assert_eq!(ledger.exec_example, None);
        assert!(!ledger.render().contains("Code Mode"));
    }

    #[test]
    fn a_degenerate_last_summary_brings_the_previous_one_back() {
        let dir = scratch("summary");
        let good = format!("## Task\nFix the thing.\n{}", "The details of what was found. ".repeat(40));
        let tool_call = "<tool_call>\n<function=exec>\n<parameter=input>\nconst r = await tools.exec_command({cmd: \"cd /testbed && git diff\"});\ntext(r.output);\n</parameter>\n</function>\n</tool_call>";
        let rollout = [call("1", "ls"), output("1", 0, "a"), compacted(&good), call("2", "ls"), output("2", 0, "a"), compacted(tool_call)].join("\n");
        let ledger = Ledger::from_rollout(&rollout, &dir, Some(""));
        assert_eq!(ledger.restored_summary.as_deref(), Some(good.trim()));
        let text = ledger.render();
        assert!(text.contains(RESTORED_LINE) && text.ends_with(good.trim()), "{text}");
        assert!(text.len() <= MAX_CHARS + 8);
        // A short one is degenerate too, and so is a line of prose before the tool call (night
        // 6's sympy-18698); two degenerate in a row reach back further.
        let prose_then_call = format!("Let me create a comprehensive handoff summary for the next LLM.\n\n{tool_call}");
        assert!(is_degenerate_summary(&prose_then_call) && is_degenerate_summary("Done.") && !is_degenerate_summary(&good));
        let rollout = [compacted(&good), compacted(&prose_then_call), compacted(tool_call)].join("\n");
        assert_eq!(Ledger::from_rollout(&rollout, &dir, Some("")).restored_summary.as_deref(), Some(good.trim()));
        // Nothing to restore when every summary is degenerate, or the last one is sound.
        assert_eq!(Ledger::from_rollout(&compacted(tool_call), &dir, Some("")).restored_summary, None);
        let rollout = [compacted(tool_call), compacted(&good)].join("\n");
        assert_eq!(Ledger::from_rollout(&rollout, &dir, Some("")).restored_summary, None);
        // A message without the preamble is read whole.
        assert_eq!(summary_body("no preamble"), "no preamble");
        // The restored summary never pushes the ledger past the spill threshold.
        let huge = Ledger {
            changed: (0..60).map(|index| format!(" M {}", "x".repeat(80) + &index.to_string())).collect(),
            restored_summary: Some("s".repeat(40_000)),
            ..Ledger::default()
        };
        let text = huge.render();
        assert!(text.len() <= MAX_CHARS + 8 && text.contains(RESTORED_LINE), "{}", text.len());
        // A ledger with nothing else still carries a restored summary.
        let only = Ledger { restored_summary: Some(good.clone()), ..Ledger::default() };
        assert!(!only.is_empty() && only.render().ends_with(&good));
        assert!(only.without_code_mode_fixes().is_empty());
    }

    #[test]
    fn the_hook_leaves_the_code_mode_lines_out_when_the_fixes_are_off() {
        assert!(code_mode_fixes_on(None) && code_mode_fixes_on(Some("")) && code_mode_fixes_on(Some("on")));
        assert!(!code_mode_fixes_on(Some("off")) && !code_mode_fixes_on(Some(" Off ")));
        let dir = scratch("fixes");
        let rollout = dir.join("rollout.jsonl");
        std::fs::write(&rollout, [cell("1", "const r = await tools.exec_command({cmd: 'ls'});\ntext(r.output);", "Script completed"), COMPACTED.to_string()].join("\n"))
            .unwrap_or_default();
        let input = json!({"source": "compact", "transcript_path": rollout, "cwd": dir, "hook_event_name": "SessionStart"}).to_string();
        // The tests run with the variable unset: the cell is restated.
        let answer: Value = serde_json::from_str(&hook(&input).unwrap_or_default()).unwrap_or_default();
        let context = answer["hookSpecificOutput"]["additionalContext"].as_str().unwrap_or_default();
        assert!(context.contains("A Code Mode `exec` cell that worked"), "{context}");
    }
}
