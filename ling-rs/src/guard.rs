//! Guards: checks on the agent's own work, enforced through Codex's hooks, off unless switched
//! on (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §29; the failure analysis they come from is
//! specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md §9).
//!
//! - **The test guard** (`DREAMFERENCE_MIGHTLING_TEST_GUARD`, `mightling_test_guard`). When a
//!   tool call leaves an existing test with fewer assertions than it had at `HEAD` (an assertion
//!   rewritten or removed, a test deleted) or with a skip it did not have, the call's result
//!   carries a note saying so, once per file; and the first stop while such a change stands is
//!   held once, asking whether the task asks for that behaviour to change. The failures it is
//!   for rewrote a test that their own change had broken (FAILURES §2: django 16100, sphinx
//!   11445, django 16950); most changed assertions in resolved runs were asked for (§9.2), so the
//!   guard asks and never refuses.
//! - **The finish check** (`DREAMFERENCE_MIGHTLING_FINISH_CHECK`, `mightling_finish_check`). The
//!   first stop of a session that changed source files is held once when no test has run on the
//!   tree as it now is; once when the last message calls failures pre-existing and no test ever
//!   ran on the code without the change; and once when the last message ends announcing a next
//!   step instead of reporting (the harness's completion nudge, as a product rule).
//!
//! Both are one `PostToolUse` and one `Stop` hook, `<this binary> guard <names>`, registered in
//! `config.toml` with their trust entries when either is on. The `PostToolUse` hook records,
//! after every tool call, a fingerprint of the working tree (`git diff HEAD` and the untracked
//! files), so an edit is seen whatever made it (the `apply_patch` tool, `sed -i`, a script), and
//! whether the call ran a test runner. Its state is `$CODEX_HOME/guards/<session>.json`, which is
//! also the record a benchmark run reads. Outside a git repository nothing happens. Every fault
//! of the guard's own lets the call or the stop through.

use std::collections::HashMap;
use std::io::Read;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;
use std::sync::LazyLock;
use std::time::Duration;
use std::time::SystemTime;

use regex_lite::Regex;
use serde_json::Value;
use serde_json::json;
use sha2::Digest;
use sha2::Sha256;

use crate::compaction::SessionHook;

/// The test guard's switch, and its key in the Dreamference config file.
pub const TEST_GUARD_ENV: &str = "DREAMFERENCE_MIGHTLING_TEST_GUARD";
pub const TEST_GUARD_SETTING: &str = "mightling_test_guard";
/// The finish check's switch, and its key in the Dreamference config file.
pub const FINISH_CHECK_ENV: &str = "DREAMFERENCE_MIGHTLING_FINISH_CHECK";
pub const FINISH_CHECK_SETTING: &str = "mightling_finish_check";
/// Both are off until a measurement makes one the default (the owner's rule of 2026-10-20).
pub const DEFAULT: bool = false;

/// The guards' names on the hook's command line, joined with `.`.
pub const TESTS: &str = "tests";
pub const FINISH: &str = "finish";

/// After every tool call: the tree's fingerprint, test runs, and the test guard's note.
pub const POST_HOOK: SessionHook = SessionHook {
    subcommand: "guard",
    matcher: "",
    timeout: 30,
    event: "PostToolUse",
    status_message: "",
    context_limit: None,
};

/// When a turn would end: the holds.
pub const STOP_HOOK: SessionHook = SessionHook {
    subcommand: "guard",
    matcher: "",
    timeout: 30,
    event: "Stop",
    status_message: "",
    context_limit: None,
};

/// The folder under `CODEX_HOME` that holds one state file per session.
pub const STATE_DIR: &str = "guards";

/// The kinds of hold, as the state records them.
pub const UNTESTED: &str = "untested";
pub const PRE_EXISTING: &str = "pre-existing";
pub const MID_WORK: &str = "mid-work";
pub const TEST_EDITS: &str = "test-edits";

/// How much of the end of a last message is read for an announcement, as the harness's
/// completion nudge reads it (`MID_WORK_TAIL` in `swe_bench_instance_run.py`).
const MID_WORK_TAIL: usize = 400;
/// Test files larger than this are not compared line by line.
const MAX_FILE_BYTES: usize = 2_000_000;
/// Files, lines and suggested tests named in one message.
const MAX_LISTED: usize = 3;
/// Fingerprints kept per session; a longer session forgets its oldest.
const MAX_SEEN: usize = 400;
/// How long a session's state file may be locked by another hook of the same session.
const LOCK_WAIT: Duration = Duration::from_secs(5);
const STALE_LOCK: Duration = Duration::from_secs(60);

// -- the switches ------------------------------------------------------------------------------

/// Whether a switch is on: the variable when set, then the config file's key, then [`DEFAULT`].
pub fn switch_on(env: Option<&str>, settings: &toml::Table, key: &str) -> bool {
    if let Some(value) = env.filter(|value| !value.is_empty()) {
        return matches!(value.to_lowercase().as_str(), "1" | "true" | "yes" | "on");
    }
    settings.get(key).and_then(toml::Value::as_bool).unwrap_or(DEFAULT)
}

/// The guards that are on, in their fixed order: [`TESTS`], then [`FINISH`].
pub fn enabled(test_env: Option<&str>, finish_env: Option<&str>, settings: &toml::Table) -> Vec<&'static str> {
    let mut on = Vec::new();
    if switch_on(test_env, settings, TEST_GUARD_SETTING) {
        on.push(TESTS);
    }
    if switch_on(finish_env, settings, FINISH_CHECK_SETTING) {
        on.push(FINISH);
    }
    on
}

/// [`enabled`] for this process: its environment and the Dreamference config file.
pub fn enabled_now() -> Vec<&'static str> {
    let settings = crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok())
        .unwrap_or_default();
    enabled(
        std::env::var(TEST_GUARD_ENV).ok().as_deref(),
        std::env::var(FINISH_CHECK_ENV).ok().as_deref(),
        &settings,
    )
}

/// Brings `config.toml` in line with the switches: both hooks with the guards on as their
/// argument (`tests`, `finish`, `tests.finish`), or neither. The argument is on the command line,
/// not read from the environment when the hook runs, because Codex gives a hook the session's
/// environment snapshot, which may leave a variable out.
pub fn register(codex_home: &Path, interactive: bool) {
    let guards = enabled_now();
    let config = codex_home.join("config.toml");
    let argument = guards.join(".");
    let on = !guards.is_empty();
    let _ = crate::compaction::register_session_hook(&config, &POST_HOOK, &argument, on);
    let _ = crate::compaction::register_session_hook(&config, &STOP_HOOK, &argument, on);
    if interactive {
        if guards.contains(&TESTS) {
            crate::notice::say(
                "test guard: on: a change that weakens an existing test is pointed out, and the first stop while it stands is held once.",
            );
        }
        if guards.contains(&FINISH) {
            crate::notice::say(
                "finish check: on: the first stop after a change no test has run on is held once, as is one that ends mid-work.",
            );
        }
        if on && codex_home.join("hooks.json").is_file() {
            crate::notice::say("guards: on, but your hooks are in hooks.json, so they are not registered; move them to config.toml.");
        }
    }
}

// -- the hook ----------------------------------------------------------------------------------

/// `ling guard <names>`: the hook. Reads Codex's JSON from stdin and prints the answer, if any.
/// Never fails: a guard that cannot decide lets the call or the stop through.
pub fn run_cli(args: &[String]) -> i32 {
    let names: Vec<String> = args
        .first()
        .map(|list| {
            list.split('.')
                .filter(|name| [TESTS, FINISH].contains(name))
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_err() {
        return 0;
    }
    if let Some(answer) = handle(&names, &input, SystemTime::now()) {
        println!("{answer}");
    }
    0
}

/// What Codex hands a hook, as far as the guards read it.
#[derive(Debug, Clone, PartialEq)]
pub struct Input {
    pub event: String,
    pub session: String,
    pub turn: String,
    pub cwd: PathBuf,
    pub transcript: Option<PathBuf>,
    pub tool_name: String,
    pub tool_input: Value,
    pub stop_hook_active: bool,
    pub last_message: String,
}

impl Input {
    pub fn parse(text: &str) -> Option<Input> {
        let value: Value = serde_json::from_str(text).ok()?;
        let text_of = |key: &str| value[key].as_str().unwrap_or_default().to_string();
        let session = text_of("session_id");
        let cwd = text_of("cwd");
        if session.is_empty() || cwd.is_empty() {
            return None;
        }
        Some(Input {
            event: text_of("hook_event_name"),
            session,
            turn: text_of("turn_id"),
            cwd: PathBuf::from(cwd),
            transcript: value["transcript_path"].as_str().filter(|path| !path.is_empty()).map(PathBuf::from),
            tool_name: text_of("tool_name"),
            tool_input: value["tool_input"].clone(),
            stop_hook_active: value["stop_hook_active"].as_bool().unwrap_or(false),
            last_message: text_of("last_assistant_message"),
        })
    }
}

/// Runs one hook call: `PostToolUse` or `Stop`, in the session's repository, with its state.
pub fn handle(guards: &[String], text: &str, now: SystemTime) -> Option<String> {
    if guards.is_empty() {
        return None;
    }
    let input = Input::parse(text)?;
    let root = git_root(&input.cwd)?;
    let folder = codex_home_of(&input)?.join(STATE_DIR);
    std::fs::create_dir_all(&folder).ok()?;
    let name: String = input
        .session
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '_' })
        .collect();
    let path = folder.join(format!("{name}.json"));
    let _lock = Lock::take(&folder.join(format!("{name}.lock")))?;
    let mut state = std::fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .map(|value| State::from_value(&value))
        .unwrap_or_default();
    state.session = input.session.clone();
    state.guards = guards.to_vec();
    let answer = match input.event.as_str() {
        "PostToolUse" => post_tool_use(&mut state, &input, &root),
        "Stop" => stop(&mut state, &input, &root),
        _ => None,
    };
    state.updated = now
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or_default();
    let _ = crate::write_atomically(&path, state.to_value().to_string().as_bytes());
    answer
}

/// The session's `CODEX_HOME`: the folder above `sessions/` in the transcript's path, which is
/// where Codex writes it, else the one this process resolves.
fn codex_home_of(input: &Input) -> Option<PathBuf> {
    if let Some(transcript) = &input.transcript {
        for ancestor in transcript.ancestors() {
            if ancestor.file_name().is_some_and(|name| name == "sessions") {
                return ancestor.parent().map(Path::to_path_buf);
            }
        }
    }
    codex_utils_home_dir::find_codex_home().ok().map(|home| home.as_path().to_path_buf())
}

/// After one tool call.
pub fn post_tool_use(state: &mut State, input: &Input, root: &Path) -> Option<String> {
    let tree = Tree::read(root)?;
    state.calls += 1;
    if state.initial.is_empty() {
        state.initial = tree.fingerprint.clone();
        state.seen.push(tree.fingerprint.clone());
    }
    let new_state = !state.seen.contains(&tree.fingerprint);
    if new_state {
        state.seen.push(tree.fingerprint.clone());
        if state.seen.len() > MAX_SEEN {
            state.seen.remove(1);
        }
        state.edits += 1;
        state.last_edit_call = state.calls;
    }
    let text = command_text(input);
    let tested = is_test_command(&text);
    let baseline = tested && (tree.fingerprint == state.initial || tree.clean || is_baseline_command(&text));
    if tested {
        state.test_runs += 1;
        state.last_test_call = state.calls;
        if !state.tested.contains(&tree.fingerprint) {
            state.tested.push(tree.fingerprint.clone());
        }
    }
    if baseline {
        state.baseline_runs += 1;
    }
    for hold in state.holds.iter_mut().filter(|hold| !hold.complied) {
        hold.complied = match hold.kind.as_str() {
            UNTESTED => tested,
            PRE_EXISTING => baseline,
            MID_WORK => true,
            _ => false,
        };
    }
    if !state.guards.iter().any(|name| name == TESTS) {
        return None;
    }
    let pending = state.holds.iter().any(|hold| hold.kind == TEST_EDITS && !hold.complied);
    if !new_state && !pending {
        return None;
    }
    let weakened = weakened(root, &tree.changed);
    for hold in state.holds.iter_mut().filter(|hold| hold.kind == TEST_EDITS) {
        let remaining = hold.lines.iter().filter(|line| weakened.iter().any(|w| w.lines.contains(line))).count() as u64;
        hold.remaining = Some(remaining);
        hold.complied = remaining == 0;
    }
    let fresh: Vec<&Weakened> = weakened.iter().filter(|w| !state.noted.contains(&w.path)).collect();
    if !new_state || fresh.is_empty() {
        return None;
    }
    for w in &fresh {
        state.noted.push(w.path.clone());
    }
    state.notes.push(json!({
        "call": state.calls,
        "files": fresh.iter().map(|w| json!({"path": w.path, "lines": w.lines})).collect::<Vec<_>>(),
    }));
    let context = note_text(&fresh);
    Some(
        json!({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}).to_string(),
    )
}

/// When the turn would end.
pub fn stop(state: &mut State, input: &Input, root: &Path) -> Option<String> {
    // A stop that follows one of our holds (or anyone's) goes through: one hold per stop, and a
    // held turn is never held again until it has done something.
    if input.stop_hook_active || state.initial.is_empty() {
        return None;
    }
    let tree = Tree::read(root)?;
    state.stops += 1;
    let edited = state.edits > 0 && tree.fingerprint != state.initial;
    let tested_now = state.tested.contains(&tree.fingerprint);
    let fired = |state: &State, kind: &str| state.holds.iter().any(|hold| hold.kind == kind);
    let finish = state.guards.iter().any(|name| name == FINISH);
    let tests = state.guards.iter().any(|name| name == TESTS);
    let code: Vec<String> = tree.changed.iter().filter(|path| is_code_path(path)).cloned().collect();
    let weakened = if tests { weakened(root, &tree.changed) } else { Vec::new() };
    state.last_stop = json!({
        "stop": state.stops,
        "edited": edited,
        "code_changed": code.len(),
        "tested_now": tested_now,
        "weakened_files": weakened.len(),
    });
    let mut items: Vec<String> = Vec::new();
    let mut holds: Vec<Hold> = Vec::new();
    let hold = |kind: &str, files: Vec<String>, lines: Vec<String>| Hold {
        kind: kind.to_string(),
        call: state.calls,
        stop: state.stops,
        turn: input.turn.clone(),
        files,
        lines,
        complied: false,
        remaining: None,
    };
    if finish && edited && !fired(state, MID_WORK) && stopped_mid_work(&input.last_message) {
        items.push(MID_WORK_TEXT.to_string());
        holds.push(hold(MID_WORK, Vec::new(), Vec::new()));
    }
    if finish && edited && !fired(state, UNTESTED) && !code.is_empty() && !tested_now {
        let suggested = suggested_tests(root, &code);
        items.push(untested_text(&code, &suggested));
        holds.push(hold(UNTESTED, code.iter().take(MAX_LISTED * 4).cloned().collect(), suggested));
    }
    if finish && edited && !fired(state, PRE_EXISTING) && claims_pre_existing(&input.last_message) && state.baseline_runs == 0 {
        items.push(PRE_EXISTING_TEXT.to_string());
        holds.push(hold(PRE_EXISTING, Vec::new(), Vec::new()));
    }
    if tests && !fired(state, TEST_EDITS) && !weakened.is_empty() {
        items.push(test_edits_text(&weakened));
        let lines: Vec<String> = weakened.iter().flat_map(|w| w.lines.clone()).collect();
        let mut held = hold(TEST_EDITS, weakened.iter().map(|w| w.path.clone()).collect(), lines);
        held.remaining = Some(held.lines.len() as u64);
        holds.push(held);
    }
    if items.is_empty() {
        return None;
    }
    state.holds.extend(holds);
    let reason = format!("Before you stop:\n{}\nThen stop with a short summary.", items.join("\n"));
    Some(json!({"decision": "block", "reason": reason}).to_string())
}

// -- the texts ---------------------------------------------------------------------------------

pub const MID_WORK_TEXT: &str =
    "- Your last message announces a next step instead of reporting what you did. Finish that work first.";

pub const PRE_EXISTING_TEXT: &str = "- You say some failures were there before your change, but no test has run on the code without it. Check: `git stash`, run the same tests, note the failing names, `git stash pop`, and compare by name. Fix every failure your change caused.";

/// The note a tool call's result carries when it weakened an existing test.
pub fn note_text(weakened: &[&Weakened]) -> String {
    let named: Vec<String> = weakened.iter().take(MAX_LISTED).map(|w| describe(w)).collect();
    let first = weakened.first().map(|w| w.path.as_str()).unwrap_or_default();
    format!(
        "Note: this changes an existing test: {}. Change an existing test only when the task asks for the behaviour it checks to change. If it does not, restore the test (`git checkout -- {first}`) and fix the code instead.",
        named.join("; ")
    )
}

fn untested_text(code: &[String], suggested: &[String]) -> String {
    let listed = list(code);
    let example = if suggested.is_empty() {
        String::new()
    } else {
        format!(" (for example {})", suggested.iter().map(|path| format!("`{path}`")).collect::<Vec<_>>().join(", "))
    };
    format!(
        "- You changed {listed}, and no test has run on the code as it is now. Run the existing tests of the modules you changed{example}. If any fail, run them once without your change (`git stash`, run, `git stash pop`) and compare by name; fix every failure your change caused."
    )
}

fn test_edits_text(weakened: &[Weakened]) -> String {
    let named: Vec<String> = weakened.iter().take(MAX_LISTED).map(describe).collect();
    format!(
        "- Your change weakens existing tests: {}. Unless the task asks for the behaviour those tests check to change, restore them (`git checkout -- <file>`) and fix the code; if it does, keep them and say in your summary which behaviour the task changes.",
        named.join("; ")
    )
}

fn describe(weakened: &Weakened) -> String {
    let lines: Vec<String> = weakened.lines.iter().take(MAX_LISTED).map(|line| format!("`{}`", clip(line, 120))).collect();
    let more = weakened.lines.len().saturating_sub(MAX_LISTED);
    let more = if more > 0 { format!(" and {more} more") } else { String::new() };
    format!("{} ({}{more})", weakened.path, lines.join(", "))
}

fn list(paths: &[String]) -> String {
    let mut named: Vec<String> = paths.iter().take(MAX_LISTED).map(|path| format!("`{path}`")).collect();
    let more = paths.len().saturating_sub(MAX_LISTED);
    if more > 0 {
        named.push(format!("{more} more"));
    }
    named.join(", ")
}

fn clip(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_string();
    }
    let cut: String = text.chars().take(limit).collect();
    format!("{cut}…")
}

// -- the rules ---------------------------------------------------------------------------------

static ANNOUNCES_WORK: LazyLock<Regex> = LazyLock::new(|| {
    // Night Shift's `ANNOUNCES_WORK` (night_shift_task_run.py), with the curly apostrophe too.
    Regex::new(
        r"(?i)\b(I['’]ll|I will|I['’]m going to|I am going to|Let me|Next,? I|I can now|I['’]m about to|I shall|now I['’]ll|I['’]ll now|going to (?:make|add|update|implement|write|create|fix))\b",
    )
    .unwrap_or_else(|_| never())
});
static LET_ME_KNOW: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\blet me know\b").unwrap_or_else(|_| never()));
/// An offer to the reader is not an announcement: "If you'd like, I'll also …", "Want me to …?".
static OFFER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(if you['’]?d like|if you want|if you prefer|would you like|want me to|shall I|should I)\b|\?\s*$")
        .unwrap_or_else(|_| never())
});
static PRE_EXISTING_CLAIM: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\bpre-?existing\b|\balready (fail|failing|failed|broken)\b|\bwere (already )?failing before\b|\bunrelated to (my|the|this) (change|fix|patch)|\bnot caused by (my|the|this) (change|fix|patch)|\bfail(s|ed|ing)? (also |even )?(on|at|before|without) (the )?(base|baseline|head|original|unmodified|my change|the change|this change)",
    )
    .unwrap_or_else(|_| never())
});
static TEST_COMMAND: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(py\.?test|unittest|nosetests|tox|nox|runtests(\.py)?|manage\.py\s+test|bin/test|sympy\.test|doctest|cargo\s+(test|nextest)|go\s+test|ctest|(npm|pnpm|yarn|bun)\s+(run\s+)?test|jest|vitest|mocha|rspec|phpunit|dotnet\s+test|swift\s+test|mix\s+test|make\s+(test|check)|(mvn|gradlew?|\./gradlew)\s+(\S+\s+)*test)\b|\bpython[0-9.]*\s+(\S+\s+)*\S*test\S*\.py\b",
    )
    .unwrap_or_else(|_| never())
});
static ASSERTION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)assert|\bexpect\s*\(|pytest\.raises|self\.fail\s*\(|\.should\b|^(async\s+)?def\s+test|^(pub\s+)?fn\s+test_")
        .unwrap_or_else(|_| never())
});
static SKIP: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)@pytest\.mark\.(skip|xfail)|@unittest\.(skip|expectedfailure)|^@skip\b|#\[ignore\]|\b(it|test|describe)\.skip\s*\(|\bxit\s*\(|pytest\.skip\s*\(|self\.skiptest\s*\(",
    )
    .unwrap_or_else(|_| never())
});

/// A pattern that matches nothing, should one of the patterns above ever fail to compile.
fn never() -> Regex {
    Regex::new(r"[^\s\S]").unwrap_or_else(|_| unreachable!("a pattern of one empty class compiles"))
}

/// Whether a last message ends in the middle of the work: an announcement of a next step in its
/// last 400 characters, "let me know" and offers to the reader aside.
pub fn stopped_mid_work(message: &str) -> bool {
    let trimmed = message.trim();
    let count = trimmed.chars().count();
    let tail: String = trimmed.chars().skip(count.saturating_sub(MID_WORK_TAIL)).collect();
    if tail.is_empty() || OFFER.is_match(&tail) {
        return false;
    }
    ANNOUNCES_WORK.is_match(&LET_ME_KNOW.replace_all(&tail, ""))
}

/// Whether a last message dismisses failures as there before the change.
pub fn claims_pre_existing(message: &str) -> bool {
    PRE_EXISTING_CLAIM.is_match(message)
}

/// Whether a command runs tests.
pub fn is_test_command(text: &str) -> bool {
    TEST_COMMAND.is_match(text)
}

/// Whether a command compares with the code as it was: a stash, or a second worktree.
fn is_baseline_command(text: &str) -> bool {
    text.contains("git stash") || text.contains("git worktree")
}

/// The text of a tool call's input that could run a command: every string in it, for any tool
/// but the patch tool and Code Mode's `exec`, whose input is a file's or a script's text (the
/// calls Code Mode makes reach this hook on their own).
pub fn command_text(input: &Input) -> String {
    if matches!(input.tool_name.as_str(), "apply_patch" | "exec" | "wait") {
        return String::new();
    }
    let mut parts = Vec::new();
    collect_strings(&input.tool_input, &mut parts);
    parts.join("\n")
}

fn collect_strings(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::String(text) => out.push(text.clone()),
        Value::Array(items) => items.iter().for_each(|item| collect_strings(item, out)),
        Value::Object(map) => map.values().for_each(|item| collect_strings(item, out)),
        _ => {}
    }
}

/// Whether a path is a test file: under a `tests`/`testing`/`__tests__`/`spec` folder, a `test`
/// folder at the top or as `src/test/` (a `test` folder deeper down is often the framework's own
/// code: `django/test/`), or named as one (`test_*.py`, `*_test.py`, `tests.py`, `conftest.py`,
/// `*_test.go`, `*.test.ts`, `*.spec.js`, `*_spec.rb`, `*Test.java`, …).
pub fn is_test_path(path: &str) -> bool {
    let parts: Vec<&str> = path.split(['/', '\\']).collect();
    let Some((name, folders)) = parts.split_last() else { return false };
    if folders.iter().any(|folder| matches!(*folder, "tests" | "testing" | "__tests__" | "spec" | "specs")) {
        return true;
    }
    if folders.first() == Some(&"test") || folders.windows(2).any(|pair| pair == ["src", "test"]) {
        return true;
    }
    let lower = name.to_lowercase();
    if (lower.starts_with("test_") && lower.ends_with(".py"))
        || lower.ends_with("_test.py")
        || lower == "tests.py"
        || lower == "conftest.py"
        || lower.ends_with("_test.go")
        || lower.ends_with("_spec.rb")
        || lower.ends_with("_test.rs")
    {
        return true;
    }
    let script = [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"];
    if script.iter().any(|ext| [".test", ".spec"].iter().any(|kind| lower.ends_with(&format!("{kind}{ext}")))) {
        return true;
    }
    ["Test.java", "Tests.java", "Test.kt", "Tests.kt", "Tests.cs", "Test.cs"].iter().any(|end| name.ends_with(end))
}

/// Whether a path is source code that is not a test.
pub fn is_code_path(path: &str) -> bool {
    const CODE: &[&str] = &[
        "py", "pyx", "rs", "go", "js", "jsx", "ts", "tsx", "mjs", "cjs", "java", "kt", "scala", "rb", "php", "c", "cc", "cpp",
        "cxx", "h", "hpp", "cs", "swift", "m", "mm", "lua", "pl", "ex", "exs", "clj", "dart", "jl", "r", "sh",
    ];
    let extension = Path::new(path).extension().and_then(|ext| ext.to_str()).unwrap_or_default().to_lowercase();
    CODE.contains(&extension.as_str()) && !is_test_path(path)
}

/// Existing tests a changed module probably has, from the tracked files: `test_<stem>.*`,
/// `<stem>_test.*`, `<stem>.test.*`, `tests/<stem>/…`; at most [`MAX_LISTED`].
fn suggested_tests(root: &Path, code: &[String]) -> Vec<String> {
    let Some(files) = git(root, &["ls-files"]) else { return Vec::new() };
    let tests: Vec<&str> = files.lines().filter(|path| is_test_path(path)).collect();
    let mut found: Vec<String> = Vec::new();
    for path in code {
        let stem = Path::new(path).file_stem().and_then(|stem| stem.to_str()).unwrap_or_default();
        let stem = if stem == "__init__" {
            Path::new(path).parent().and_then(Path::file_name).and_then(|name| name.to_str()).unwrap_or_default()
        } else {
            stem
        };
        if stem.len() < 3 {
            continue;
        }
        let stem = stem.to_lowercase();
        for test in &tests {
            let lower = test.to_lowercase();
            let name = lower.rsplit('/').next().unwrap_or_default();
            let matches = name.starts_with(&format!("test_{stem}"))
                || name.starts_with(&format!("{stem}_test"))
                || name.starts_with(&format!("{stem}.test"))
                || name.starts_with(&format!("{stem}.spec"))
                || lower.contains(&format!("/{stem}/"))
                || lower.contains(&format!("/{stem}_tests/"));
            if matches && !found.iter().any(|seen| seen == test) {
                found.push(test.to_string());
                if found.len() >= MAX_LISTED {
                    return found;
                }
            }
        }
    }
    found
}

// -- the tree ----------------------------------------------------------------------------------

/// The working tree as the guards see it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tree {
    /// SHA-256 of `git diff HEAD` and of each untracked file's name, size and time.
    pub fingerprint: String,
    /// No tracked file differs from `HEAD`.
    pub clean: bool,
    /// The tracked files that differ from `HEAD`, deleted ones included.
    pub changed: Vec<String>,
}

impl Tree {
    pub fn read(root: &Path) -> Option<Tree> {
        let diff = git(root, &["diff", "HEAD", "--no-ext-diff", "--no-color", "--binary"])?;
        let names = git(root, &["diff", "HEAD", "--no-ext-diff", "--name-only"])?;
        let untracked = git(root, &["ls-files", "--others", "--exclude-standard"])?;
        let mut hasher = Sha256::new();
        hasher.update(diff.as_bytes());
        for path in untracked.lines() {
            hasher.update(b"\0");
            hasher.update(path.as_bytes());
            if let Ok(meta) = std::fs::metadata(root.join(path)) {
                let at = meta
                    .modified()
                    .ok()
                    .and_then(|time| time.duration_since(SystemTime::UNIX_EPOCH).ok())
                    .map(|elapsed| elapsed.as_nanos())
                    .unwrap_or_default();
                hasher.update(format!(":{}:{at}", meta.len()).as_bytes());
            }
        }
        let fingerprint: String = hasher.finalize().iter().map(|byte| format!("{byte:02x}")).collect();
        Some(Tree {
            fingerprint,
            clean: diff.is_empty(),
            changed: names.lines().filter(|line| !line.is_empty()).map(str::to_string).collect(),
        })
    }
}

/// An existing test file and what it lost against `HEAD`: assertion lines (and test functions)
/// it no longer has, as `- <line>`, and skips it gained, as `+ <line>`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Weakened {
    pub path: String,
    pub lines: Vec<String>,
}

/// The tracked test files among `changed` that lost assertions or gained skips.
pub fn weakened(root: &Path, changed: &[String]) -> Vec<Weakened> {
    let mut found = Vec::new();
    for path in changed.iter().filter(|path| is_test_path(path)) {
        let Some(before) = git(root, &["show", &format!("HEAD:{path}")]) else { continue };
        if before.len() > MAX_FILE_BYTES {
            continue;
        }
        let after = std::fs::read(root.join(path)).map(|bytes| String::from_utf8_lossy(&bytes).into_owned()).unwrap_or_default();
        let lines = compare(&before, &after);
        if !lines.is_empty() {
            found.push(Weakened { path: path.clone(), lines });
        }
    }
    found
}

/// What a test file lost from `before` to `after`, line by line, ignoring indentation: every
/// assertion line of `before` with no equal line left in `after`, and every skip line of `after`
/// that `before` did not have. Comment lines do not count.
pub fn compare(before: &str, after: &str) -> Vec<String> {
    let counts = |text: &str| {
        let mut counts: HashMap<String, usize> = HashMap::new();
        for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
            *counts.entry(line.to_string()).or_default() += 1;
        }
        counts
    };
    let comment = |line: &str| (line.starts_with('#') && !line.starts_with("#[")) || line.starts_with("//");
    let mut left = counts(after);
    let mut lost = Vec::new();
    for line in before.lines().map(str::trim).filter(|line| !line.is_empty() && !comment(line)) {
        if !ASSERTION.is_match(line) {
            continue;
        }
        match left.get_mut(line) {
            Some(count) if *count > 0 => *count -= 1,
            _ => lost.push(format!("- {line}")),
        }
    }
    let mut had = counts(before);
    for line in after.lines().map(str::trim).filter(|line| !line.is_empty() && !comment(line)) {
        if !SKIP.is_match(line) {
            continue;
        }
        match had.get_mut(line) {
            Some(count) if *count > 0 => *count -= 1,
            _ => lost.push(format!("+ {line}")),
        }
    }
    lost
}

/// The top of the repository `cwd` is in, or `None` outside one.
fn git_root(cwd: &Path) -> Option<PathBuf> {
    git(cwd, &["rev-parse", "--show-toplevel"]).map(|text| PathBuf::from(text.trim()))
}

/// A git command's output, or `None` when it failed.
fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let output = ling_safegit::output(dir, args).ok()?;
    output.status.success().then(|| String::from_utf8_lossy(&output.stdout).into_owned())
}

// -- the state ---------------------------------------------------------------------------------

/// One hold, as recorded: what it held, and whether the agent then did what it asked.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Hold {
    pub kind: String,
    /// The tool calls before it, and which stop of the session it held.
    pub call: u64,
    pub stop: u64,
    pub turn: String,
    pub files: Vec<String>,
    pub lines: Vec<String>,
    /// `untested`: a test ran after it; `pre-existing`: a test ran on the code without the
    /// change; `mid-work`: a tool call followed; `test-edits`: every line it named is back.
    pub complied: bool,
    /// `test-edits`: the lines it named that are still missing.
    pub remaining: Option<u64>,
}

/// A session's state: `$CODEX_HOME/guards/<session>.json`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct State {
    pub session: String,
    pub guards: Vec<String>,
    pub updated: u64,
    pub calls: u64,
    pub stops: u64,
    /// The tree's fingerprint after the first tool call, and every one seen since.
    pub initial: String,
    pub seen: Vec<String>,
    /// Tool calls that left the tree in a state not seen before, and the last one's number.
    pub edits: u64,
    pub last_edit_call: u64,
    /// Fingerprints of the trees a test ran on.
    pub tested: Vec<String>,
    pub test_runs: u64,
    pub last_test_call: u64,
    /// Test runs on the code without the change.
    pub baseline_runs: u64,
    /// Test files the test guard has pointed out, and its notes.
    pub noted: Vec<String>,
    pub notes: Vec<Value>,
    pub holds: Vec<Hold>,
    /// What the last stop saw: `edited`, `code_changed`, `tested_now`, `weakened_files`.
    pub last_stop: Value,
}

impl State {
    pub fn to_value(&self) -> Value {
        json!({
            "version": 1,
            "session": self.session,
            "guards": self.guards,
            "updated": self.updated,
            "calls": self.calls,
            "stops": self.stops,
            "initial": self.initial,
            "seen": self.seen,
            "edits": self.edits,
            "last_edit_call": self.last_edit_call,
            "tested": self.tested,
            "test_runs": self.test_runs,
            "last_test_call": self.last_test_call,
            "baseline_runs": self.baseline_runs,
            "noted": self.noted,
            "notes": self.notes,
            "holds": self.holds.iter().map(|hold| json!({
                "kind": hold.kind,
                "call": hold.call,
                "stop": hold.stop,
                "turn": hold.turn,
                "files": hold.files,
                "lines": hold.lines,
                "complied": hold.complied,
                "remaining": hold.remaining,
            })).collect::<Vec<_>>(),
            "last_stop": self.last_stop,
        })
    }

    pub fn from_value(value: &Value) -> State {
        let number = |value: &Value, key: &str| value[key].as_u64().unwrap_or_default();
        let text = |value: &Value, key: &str| value[key].as_str().unwrap_or_default().to_string();
        let texts = |value: &Value, key: &str| -> Vec<String> {
            value[key].as_array().map(|items| items.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default()
        };
        State {
            session: text(value, "session"),
            guards: texts(value, "guards"),
            updated: number(value, "updated"),
            calls: number(value, "calls"),
            stops: number(value, "stops"),
            initial: text(value, "initial"),
            seen: texts(value, "seen"),
            edits: number(value, "edits"),
            last_edit_call: number(value, "last_edit_call"),
            tested: texts(value, "tested"),
            test_runs: number(value, "test_runs"),
            last_test_call: number(value, "last_test_call"),
            baseline_runs: number(value, "baseline_runs"),
            noted: texts(value, "noted"),
            notes: value["notes"].as_array().cloned().unwrap_or_default(),
            holds: value["holds"]
                .as_array()
                .map(|items| {
                    items
                        .iter()
                        .map(|hold| Hold {
                            kind: text(hold, "kind"),
                            call: number(hold, "call"),
                            stop: number(hold, "stop"),
                            turn: text(hold, "turn"),
                            files: texts(hold, "files"),
                            lines: texts(hold, "lines"),
                            complied: hold["complied"].as_bool().unwrap_or(false),
                            remaining: hold["remaining"].as_u64(),
                        })
                        .collect()
                })
                .unwrap_or_default(),
            last_stop: value["last_stop"].clone(),
        }
    }
}

/// A session's lock file, so two hooks of one session (parallel tool calls) do not lose each
/// other's update. Removed when dropped; one older than a minute is a crashed hook's.
struct Lock(PathBuf);

impl Lock {
    fn take(path: &Path) -> Option<Lock> {
        let started = SystemTime::now();
        loop {
            match std::fs::OpenOptions::new().write(true).create_new(true).open(path) {
                Ok(_) => return Some(Lock(path.to_path_buf())),
                Err(_) => {
                    let stale = std::fs::metadata(path)
                        .and_then(|meta| meta.modified())
                        .ok()
                        .and_then(|at| at.elapsed().ok())
                        .is_some_and(|age| age > STALE_LOCK);
                    if stale {
                        let _ = std::fs::remove_file(path);
                        continue;
                    }
                    if !started.elapsed().is_ok_and(|waited| waited <= LOCK_WAIT) {
                        return None;
                    }
                    std::thread::sleep(Duration::from_millis(20));
                }
            }
        }
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mightling-guard-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap_or_default();
        dir.canonicalize().unwrap_or(dir)
    }

    fn run_git(dir: &Path, args: &[&str]) {
        let _ = Command::new("git").arg("-C").arg(dir).args(args).stdout(Stdio::null()).stderr(Stdio::null()).status();
    }

    /// A repository with one module and its test, committed.
    fn repository(name: &str) -> (PathBuf, PathBuf) {
        let dir = scratch(name);
        let repo = dir.join("repo");
        std::fs::create_dir_all(repo.join("pkg")).unwrap_or_default();
        std::fs::create_dir_all(repo.join("tests")).unwrap_or_default();
        std::fs::write(repo.join("pkg/thing.py"), "def area(w, h):\n    return w * h\n").unwrap_or_default();
        std::fs::write(
            repo.join("tests/test_thing.py"),
            "from pkg.thing import area\n\ndef test_area():\n    assert area(2, 3) == 6\n    assert area(0, 3) == 0\n",
        )
        .unwrap_or_default();
        run_git(&repo, &["init", "-q"]);
        run_git(&repo, &["-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"]);
        run_git(&repo, &["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"]);
        let home = dir.join("codex-home");
        std::fs::create_dir_all(home.join("sessions/2026/10/20")).unwrap_or_default();
        (repo, home)
    }

    fn post(home: &Path, repo: &Path, guards: &str, tool: &str, input: Value) -> Option<String> {
        let event = json!({
            "session_id": "s-1", "turn_id": "t-1", "cwd": repo, "hook_event_name": "PostToolUse",
            "transcript_path": home.join("sessions/2026/10/20/rollout-s-1.jsonl"),
            "tool_name": tool, "tool_input": input, "tool_response": "", "tool_use_id": "c",
        });
        handle(&names(guards), &event.to_string(), SystemTime::now())
    }

    fn stop_event(home: &Path, repo: &Path, guards: &str, message: &str, active: bool) -> Option<String> {
        let event = json!({
            "session_id": "s-1", "turn_id": "t-1", "cwd": repo, "hook_event_name": "Stop",
            "transcript_path": home.join("sessions/2026/10/20/rollout-s-1.jsonl"),
            "stop_hook_active": active, "last_assistant_message": message,
        });
        handle(&names(guards), &event.to_string(), SystemTime::now())
    }

    fn names(guards: &str) -> Vec<String> {
        guards.split('.').map(str::to_string).collect()
    }

    fn state(home: &Path) -> State {
        let text = std::fs::read_to_string(home.join("guards/s-1.json")).unwrap_or_default();
        State::from_value(&serde_json::from_str(&text).unwrap_or(Value::Null))
    }

    fn reason(answer: &Option<String>) -> String {
        answer
            .as_deref()
            .and_then(|text| serde_json::from_str::<Value>(text).ok())
            .and_then(|value| value["reason"].as_str().map(str::to_string))
            .unwrap_or_default()
    }

    #[test]
    fn the_switches_are_off_unless_set_and_the_variable_wins() {
        let none = toml::Table::new();
        assert!(enabled(None, None, &none).is_empty());
        let file: toml::Table = "mightling_test_guard = true\nmightling_finish_check = true\n".parse().unwrap_or_default();
        assert_eq!(enabled(None, None, &file), vec![TESTS, FINISH]);
        assert_eq!(enabled(Some("0"), Some("on"), &file), vec![FINISH]);
        assert_eq!(enabled(Some("1"), None, &none), vec![TESTS]);
    }

    #[test]
    fn test_paths_and_code_paths() {
        for path in ["tests/test_x.py", "pkg/tests/x.py", "x_test.go", "a/b.test.ts", "web/c.spec.js", "conftest.py", "Foo/BarTest.java", "test/unit/a.js", "core/src/test/java/A.java"] {
            assert!(is_test_path(path), "{path}");
        }
        for path in ["pkg/thing.py", "src/lib.rs", "contest.py", "pkg/testament.py", "django/test/testcases.py"] {
            assert!(!is_test_path(path), "{path}");
        }
        assert!(is_code_path("django/db/models/query.py"));
        assert!(!is_code_path("tests/test_query.py"));
        assert!(!is_code_path("docs/index.rst"));
    }

    #[test]
    fn a_rewritten_assertion_a_deleted_test_and_an_added_skip_are_weakening() {
        let before = "def test_a():\n    assert f() == 1\n    assert g() == 2\n\ndef test_b():\n    assert h()\n";
        let after = "def test_a():\n    assert f() == 1\n    assert g() in (2, 3)\n";
        assert_eq!(
            compare(before, after),
            vec!["- assert g() == 2", "- def test_b():", "- assert h()"]
        );
        let skipped = "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert f() == 1\n";
        assert_eq!(compare("import pytest\ndef test_a():\n    assert f() == 1\n", skipped), vec!["+ @pytest.mark.skip"]);
        // Moving and re-indenting lines, and adding assertions, weakens nothing.
        let moved = "class T:\n    def test_a(self):\n        assert g() == 2\n        assert f() == 1\n        assert k()\n";
        assert_eq!(compare("def test_a(self):\n    assert f() == 1\n    assert g() == 2\n", moved), Vec::<String>::new());
        // Comments do not count.
        assert!(compare("# assert old\nassert x\n", "assert x\n").is_empty());
    }

    #[test]
    fn test_commands_and_last_messages() {
        for command in ["python -m pytest tests/x.py -q", "./tests/runtests.py utils_tests", "bin/test sympy/sets", "cargo test -p a", "npm run test", "python tests/test_thing.py", "cd /testbed && pytest -x 2>&1 | tail -5"] {
            assert!(is_test_command(command), "{command}");
        }
        for command in ["cat tests/test_x.py", "git diff", "python reproduce.py", "sed -n 1,20p pkg/thing.py"] {
            assert!(!is_test_command(command), "{command}");
        }
        assert!(stopped_mid_work("I changed the filter. Wait, let me re-read the compiler code"));
        assert!(stopped_mid_work("Tests fail with errors=8. Let me check WhereNode"));
        assert!(!stopped_mid_work("Fixed `area` for zero widths; all tests in tests/test_thing.py pass. Let me know if you want more."));
        assert!(!stopped_mid_work("Done. If you'd like, I'll also update the docs."));
        assert!(!stopped_mid_work("Done. Want me to add a test as well?"));
        assert!(claims_pre_existing("The 2 remaining failures are pre-existing (URL test data)."));
        assert!(claims_pre_existing("These tests fail on the base commit too."));
        assert!(!claims_pre_existing("All tests pass."));
    }

    #[test]
    fn nothing_happens_outside_a_repository_or_with_no_guard() {
        let dir = scratch("outside");
        let home = dir.join("codex-home");
        std::fs::create_dir_all(home.join("sessions")).unwrap_or_default();
        assert_eq!(post(&home, &dir, "finish", "Bash", json!({"command": "ls"})), None);
        assert!(!home.join("guards").exists());
        let (repo, home) = repository("no-guard");
        let event = json!({"session_id": "s-1", "cwd": repo, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}});
        assert_eq!(handle(&[], &event.to_string(), SystemTime::now()), None);
        assert!(!home.join("guards").exists());
    }

    #[test]
    fn an_untested_change_is_held_once_and_a_test_run_complies() {
        let (repo, home) = repository("untested");
        assert_eq!(post(&home, &repo, "finish", "Bash", json!({"command": "cat pkg/thing.py"})), None);
        std::fs::write(repo.join("pkg/thing.py"), "def area(w, h):\n    return max(w, 0) * h\n").unwrap_or_default();
        assert_eq!(post(&home, &repo, "finish", "apply_patch", json!({"command": "*** Begin Patch"})), None);
        let held = stop_event(&home, &repo, "finish", "Changed area to clamp widths.", false);
        let text = reason(&held);
        assert!(text.starts_with("Before you stop:"), "{text}");
        assert!(text.contains("`pkg/thing.py`") && text.contains("`tests/test_thing.py`"), "{text}");
        // The stop that follows a hold goes through, and the hold is not repeated.
        assert_eq!(stop_event(&home, &repo, "finish", "Done.", true), None);
        assert_eq!(stop_event(&home, &repo, "finish", "Done.", false), None);
        assert_eq!(post(&home, &repo, "finish", "Bash", json!({"command": "python -m pytest tests -q"})), None);
        let recorded = state(&home);
        assert_eq!(recorded.holds.len(), 1);
        assert_eq!(recorded.holds[0].kind, UNTESTED);
        assert!(recorded.holds[0].complied);
        assert_eq!(recorded.edits, 1);
        assert_eq!(recorded.test_runs, 1);
    }

    #[test]
    fn a_tested_change_and_a_read_only_session_are_not_held() {
        let (repo, home) = repository("tested");
        assert_eq!(post(&home, &repo, "finish", "Bash", json!({"command": "ls"})), None);
        assert_eq!(stop_event(&home, &repo, "finish", "Nothing to change. I'll wait.", false), None);
        std::fs::write(repo.join("pkg/thing.py"), "def area(w, h):\n    return abs(w) * h\n").unwrap_or_default();
        assert_eq!(post(&home, &repo, "finish", "Bash", json!({"command": "sed -i s/x/y/ pkg/thing.py && pytest -q"})), None);
        assert_eq!(stop_event(&home, &repo, "finish", "Fixed; the tests pass.", false), None);
        // A stash and its pop come back to a tree already seen: not a new edit, still tested.
        run_git(&repo, &["stash", "-q"]);
        assert_eq!(post(&home, &repo, "finish", "Bash", json!({"command": "git stash && pytest -q"})), None);
        run_git(&repo, &["stash", "pop", "-q"]);
        assert_eq!(post(&home, &repo, "finish", "Bash", json!({"command": "git stash pop"})), None);
        assert_eq!(stop_event(&home, &repo, "finish", "The one failure is pre-existing.", false), None);
        let recorded = state(&home);
        assert_eq!(recorded.edits, 1);
        assert!(recorded.holds.is_empty());
        assert_eq!(recorded.baseline_runs, 1);
    }

    #[test]
    fn a_pre_existing_claim_with_no_baseline_and_a_mid_work_stop_are_held_together() {
        let (repo, home) = repository("claims");
        assert_eq!(post(&home, &repo, "finish", "Bash", json!({"command": "ls"})), None);
        std::fs::write(repo.join("pkg/thing.py"), "def area(w, h):\n    return w * h + 0\n").unwrap_or_default();
        assert_eq!(post(&home, &repo, "finish", "Bash", json!({"command": "pytest -q"})), None);
        let text = reason(&stop_event(&home, &repo, "finish", "Two failures are pre-existing. Let me check the other backend", false));
        assert!(text.contains("announces a next step"), "{text}");
        assert!(text.contains("before your change"), "{text}");
        assert!(!text.contains("no test has run on the code as it is now"), "{text}");
        let kinds: Vec<String> = state(&home).holds.iter().map(|hold| hold.kind.clone()).collect();
        assert_eq!(kinds, vec![MID_WORK.to_string(), PRE_EXISTING.to_string()]);
    }

    #[test]
    fn a_weakened_test_gets_a_note_once_and_its_stop_held_until_restored() {
        let (repo, home) = repository("weakened");
        assert_eq!(post(&home, &repo, "tests", "Bash", json!({"command": "ls"})), None);
        std::fs::write(
            repo.join("tests/test_thing.py"),
            "from pkg.thing import area\n\ndef test_area():\n    assert area(2, 3) == 6\n    assert area(0, 3) in (0, None)\n",
        )
        .unwrap_or_default();
        let note = post(&home, &repo, "tests", "Bash", json!({"command": "sed -i ... tests/test_thing.py"})).unwrap_or_default();
        let value: Value = serde_json::from_str(&note).unwrap_or(Value::Null);
        let context = value["hookSpecificOutput"]["additionalContext"].as_str().unwrap_or_default();
        assert_eq!(value["hookSpecificOutput"]["hookEventName"], "PostToolUse");
        assert!(context.contains("tests/test_thing.py") && context.contains("assert area(0, 3) == 0"), "{context}");
        // A second edit to the same file is not noted again.
        std::fs::write(repo.join("tests/test_thing.py"), "def test_area():\n    pass\n").unwrap_or_default();
        assert_eq!(post(&home, &repo, "tests", "Bash", json!({"command": "python - <<EOF"})), None);
        let text = reason(&stop_event(&home, &repo, "tests", "Done.", false));
        assert!(text.contains("weakens existing tests") && text.contains("tests/test_thing.py"), "{text}");
        run_git(&repo, &["checkout", "--", "tests/test_thing.py"]);
        assert_eq!(post(&home, &repo, "tests", "Bash", json!({"command": "git checkout -- tests/test_thing.py"})), None);
        let recorded = state(&home);
        assert_eq!(recorded.notes.len(), 1);
        assert_eq!(recorded.holds.len(), 1);
        assert_eq!(recorded.holds[0].remaining, Some(0));
        assert!(recorded.holds[0].complied);
        assert_eq!(stop_event(&home, &repo, "tests", "Done.", false), None);
    }

    #[test]
    fn the_test_guard_alone_never_holds_an_untested_change() {
        let (repo, home) = repository("tests-only");
        assert_eq!(post(&home, &repo, "tests", "Bash", json!({"command": "ls"})), None);
        std::fs::write(repo.join("pkg/thing.py"), "def area(w, h):\n    return h * w\n").unwrap_or_default();
        assert_eq!(post(&home, &repo, "tests", "Bash", json!({"command": "sed -i"})), None);
        assert_eq!(stop_event(&home, &repo, "tests", "Done. Let me check", false), None);
    }

    #[test]
    fn the_state_survives_a_round_trip() {
        let mut state = State { session: "s".into(), calls: 4, initial: "f".into(), ..State::default() };
        state.holds.push(Hold { kind: UNTESTED.into(), call: 3, files: vec!["a.py".into()], remaining: Some(2), ..Hold::default() });
        state.last_stop = json!({"edited": true});
        assert_eq!(State::from_value(&state.to_value()), state);
    }

    #[test]
    fn the_hooks_are_registered_side_by_side_and_removed_together() {
        let path = Path::new("/h/config.toml");
        let command = "/opt/ling guard tests.finish";
        let post = crate::compaction::with_session_hook("", path, command, &POST_HOOK, true).unwrap_or_default();
        let both = crate::compaction::with_session_hook(&post, path, command, &STOP_HOOK, true).unwrap_or_default();
        let parsed: toml::Table = both.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["PostToolUse"][0]["hooks"][0]["command"].as_str(), Some(command));
        assert!(parsed["hooks"]["PostToolUse"][0].get("matcher").is_none());
        assert_eq!(parsed["hooks"]["Stop"][0]["hooks"][0]["command"].as_str(), Some(command));
        assert_eq!(
            parsed["hooks"]["state"]["/h/config.toml:stop:0:0"]["trusted_hash"].as_str(),
            Some(crate::compaction::hook_hash_for(command, &STOP_HOOK).as_str())
        );
        assert_eq!(
            parsed["hooks"]["state"]["/h/config.toml:post_tool_use:0:0"]["trusted_hash"].as_str(),
            Some(crate::compaction::hook_hash_for(command, &POST_HOOK).as_str())
        );
        // Another set of guards rewrites the command in place; off removes both.
        let other = crate::compaction::with_session_hook(&both, path, "/opt/ling guard finish", &STOP_HOOK, true).unwrap_or_default();
        let parsed: toml::Table = other.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["Stop"].as_array().map(Vec::len), Some(1));
        let off = crate::compaction::with_session_hook(&other, path, "/opt/ling guard", &STOP_HOOK, false).unwrap_or_default();
        let off = crate::compaction::with_session_hook(&off, path, "/opt/ling guard", &POST_HOOK, false).unwrap_or_default();
        assert!(off.trim().is_empty(), "{off}");
    }

    /// A hook somebody else registered under the same events (the SWE-bench issue gate) keeps
    /// its group and its trust entry, and ours follows it.
    #[test]
    fn a_hook_already_under_stop_keeps_its_place() {
        let path = Path::new("/h/config.toml");
        let existing = "[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = \"command\"\ncommand = \"python3 /opt/ling-issue-gate/issue_gate.py stop\"\ntimeout = 30\n\n[hooks.state.\"/h/config.toml:stop:0:0\"]\ntrusted_hash = \"sha256:gate\"\n";
        let updated = crate::compaction::with_session_hook(existing, path, "/opt/ling guard finish", &STOP_HOOK, true).unwrap_or_default();
        let parsed: toml::Table = updated.parse().unwrap_or_default();
        assert_eq!(parsed["hooks"]["Stop"].as_array().map(Vec::len), Some(2));
        assert_eq!(parsed["hooks"]["state"]["/h/config.toml:stop:0:0"]["trusted_hash"].as_str(), Some("sha256:gate"));
        assert!(parsed["hooks"]["state"].get("/h/config.toml:stop:1:0").is_some());
    }

    #[test]
    fn the_guard_hooks_trust_hashes_are_the_ones_codex_computes_from_its_own_types() {
        use codex_config::HookHandlerConfig;
        use codex_config::MatcherGroup;
        #[derive(serde::Serialize)]
        struct Identity {
            event_name: &'static str,
            #[serde(flatten)]
            group: MatcherGroup,
        }
        for (hook, label) in [(POST_HOOK, "post_tool_use"), (STOP_HOOK, "stop")] {
            let command = "/opt/ling guard tests.finish";
            let handler = HookHandlerConfig::Command {
                command: command.to_string(),
                command_windows: None,
                timeout_sec: Some(hook.timeout as u64),
                r#async: false,
                status_message: None,
                additional_context_limit: None,
            };
            let group = MatcherGroup { matcher: None, hooks: vec![handler] };
            let value = toml::Value::try_from(Identity { event_name: label, group }).unwrap_or(toml::Value::Boolean(false));
            assert_eq!(crate::compaction::hook_hash_for(command, &hook), codex_config::version_for_toml(&value), "{label}");
            assert_eq!(hook.event_label(), label);
        }
    }
}
