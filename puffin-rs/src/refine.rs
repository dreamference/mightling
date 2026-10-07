//! Refine mode: study first, then do (specs/DREAMFERENCE_PUFFIN_REFINE.md).
//!
//! A new task is first given to a session that studies it and answers with a refined description
//! (intent, requirements as observable results, every code path, edge cases, what must not change,
//! acceptance checks) without changing anything. A fresh session then does the task, given the task
//! verbatim and that description. On SWE-bench this solved 20 of 24 against 16 and 17 without it
//! (spec §2), at about two and a half times the time; it is off by default until the 100-task pair
//! decides (`DEFAULT`, and `DEFAULT_PUFFIN_REFINE` in Python, which a test keeps equal).
//!
//! The setting is `--refine`/`--no-refine` on the command line, then `DREAMFERENCE_PUFFIN_REFINE`,
//! then `puffin_refine` in the Dreamference config file. The flag is turned into the variable first
//! thing in `main()` ([`export_flag`], from `home::use_puffin_home`), before any thread exists, so
//! every process below this one (the study step, Codex's hooks) reads one setting.
//!
//! No Codex patch is needed for either surface:
//!
//! - **`puffin exec`** ([`around_exec`]): the launcher runs the study step as a child `puffin exec`
//!   (read-only sandbox, ephemeral, the description taken from `-o`) and then hands Codex the fix
//!   prompt: the task, then the rules and the description. The child is told which step it is by
//!   `PUFFIN_REFINE_STEP=study` and composes the study prompt itself, because only a launch knows
//!   whether its session has the code index's tools.
//! - **The interactive session** ([`hook`]): a `UserPromptSubmit` hook, `puffin refine hook`,
//!   registered in `config.toml` with its trust entry like the ledger. On the first prompt of a new
//!   interactive thread it runs the same study step and returns the rules and the description as
//!   `additionalContext`, so the thread, fresh at that point, does the task with them. Later prompts
//!   of the thread are conversation, not new tasks, and pass through untouched.
//!
//! Night Shift runs its two steps itself (dreamference/night_shift), and SWE-bench's `--refine`
//! arm too; both set `DREAMFERENCE_PUFFIN_REFINE=off` for the sessions they start. All of them
//! compose from `prompts/refine.md`, which the Python side carries byte for byte.

use std::ffi::OsString;
use std::io::IsTerminal;
use std::io::Read;
use std::io::Write;
use std::os::fd::AsFd;
use std::path::Path;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;
use std::time::Instant;

use clap::Command;
use serde_json::Value;
use serde_json::json;
use sha2::Digest;
use sha2::Sha256;

use crate::compaction::SessionHook;

/// The environment variable: `on`/`off` (also `1`, `true`, `yes` and their opposites).
pub const ENV: &str = "DREAMFERENCE_PUFFIN_REFINE";

/// The key in the Dreamference config file.
pub const KEY: &str = "puffin_refine";

/// Off until the 100-task SWE-bench pair shows the gain holds (spec §3). Switching refine mode on
/// for everyone is this line and `DEFAULT_PUFFIN_REFINE` in dreamference_config.py.
pub const DEFAULT: bool = false;

/// The command-line switches, taken off the command line before Codex parses it.
pub const FLAG_ON: &str = "--refine";
pub const FLAG_OFF: &str = "--no-refine";

/// Set on the study step's process: it composes the study prompt and never refines again.
pub const STEP_ENV: &str = "PUFFIN_REFINE_STEP";
pub const STUDY: &str = "study";

/// The texts, one piece per marker; dreamference/night_shift/refine_prompt.py has the same.
pub const TEXTS: &str = include_str!("../prompts/refine.md");

/// What the study step is told about writing, in its read-only sandbox.
pub const READ_ONLY_WRITES: &str =
    "this step runs in a read-only sandbox, so a command that writes a file fails; that is expected.";

/// The same, when the user chose Full Access and there is no sandbox to make the step read-only.
pub const UNSANDBOXED_WRITES: &str =
    "the second step works in this same tree, so leave it as you found it. Put scratch files in /tmp.";

/// Full Access on Codex's command line: the study step keeps it (there may be no sandbox to run).
const FULL_ACCESS_FLAGS: &[&str] = &["--dangerously-bypass-approvals-and-sandbox", "--yolo"];

/// Options the study step must not inherit, each with its value: its own sandbox, its own output
/// file, and no output schema (the description is prose).
const STUDY_DROPS_WITH_VALUE: &[&str] = &["-s", "--sandbox", "-o", "--output-last-message", "--output-schema"];

/// Flags the study step must not inherit: JSON events (its output goes to stderr or a log, and a
/// second `thread.started` would confuse a reader of the first), and sandbox shorthands.
const STUDY_DROPS: &[&str] = &["--json", "--experimental-json", "--approve-for-me", "--not-so-yolo", "--full-auto"];

/// No time limit of its own (the user's decision, 2026-10-07): thirty days is Codex's timeout in
/// form only. Interrupting the turn (Esc) ends the hook and its study step with it: Codex runs a
/// hook in a process group of its own and kills the group.
pub const HOOK_TIMEOUT_S: i64 = 30 * 24 * 3600;

/// The interactive session's hook: `<this binary> refine hook`, before each prompt is sent.
/// `additionalContextLimit = 0` keeps the description in the context whatever its length, where
/// Codex would otherwise move anything over 2,500 tokens to a file.
pub const REFINE_HOOK: SessionHook = SessionHook {
    subcommand: "refine",
    matcher: "",
    timeout: HOOK_TIMEOUT_S,
    event: "UserPromptSubmit",
    status_message: "Refine: studying the task in a separate read-only session first",
    context_limit: Some(0),
};

/// Study-step files (`$CODEX_HOME/refine/<session>.{md,log}`) older than this go at the next hook.
const KEEP: Duration = Duration::from_secs(14 * 24 * 3600);

// -- the texts ---------------------------------------------------------------------------------

/// One piece of [`TEXTS`]: the lines between its marker and the next, without the final break.
pub fn piece(name: &str) -> String {
    let mut current: Option<&str> = None;
    let mut lines: Vec<&str> = Vec::new();
    for line in TEXTS.split('\n') {
        let marker = line
            .strip_prefix("<!-- ")
            .and_then(|rest| rest.strip_suffix(" -->"))
            .filter(|word| !word.is_empty() && word.chars().all(|c| c.is_ascii_lowercase() || c == '-'));
        if let Some(marker) = marker {
            if current == Some(name) {
                return lines.join("\n");
            }
            current = Some(marker);
            lines.clear();
        } else if current == Some(name) {
            lines.push(line);
        }
    }
    String::new()
}

fn task_piece(name: &str) -> String {
    piece(name).replace("{subject}", "task")
}

/// The study step's prompt for `task`. `writes` says what writing does in this step
/// ([`READ_ONLY_WRITES`], [`UNSANDBOXED_WRITES`]); `code_index` adds the sentence that sends the
/// study to the code index's tools for every path.
pub fn study_prompt(task: &str, writes: &str, code_index: bool) -> String {
    let hint = if code_index { format!("{}\n", task_piece("study-code-index")) } else { String::new() };
    // `{task}` last, so the task's own text is never searched for placeholders.
    piece("study")
        .replace("{intro}", &task_piece("study-intro"))
        .replace("{writes}", writes)
        .replace("{code_index}", &hint)
        .replace("{sections}", &piece("study-sections"))
        .replace("{task}", task)
}

/// What the doing step is given after the task: the rules, then the description.
pub fn fix_block(refined: &str) -> String {
    let refined = refined.trim();
    let refined = if refined.is_empty() { task_piece("no-refined") } else { refined.to_string() };
    piece("fix")
        .replace("{rules}", &task_piece("fix-rules"))
        .replace("{heading}", &piece("refined-heading"))
        .replace("{refined}", &refined)
}

/// The doing step's prompt in `puffin exec`: the task as given, then [`fix_block`].
pub fn fix_prompt(task: &str, refined: &str) -> String {
    format!("{task}\n\n{}", fix_block(refined))
}

// -- the setting -------------------------------------------------------------------------------

fn settings() -> toml::Table {
    crate::config_file()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.parse::<toml::Table>().ok())
        .unwrap_or_default()
}

/// `DREAMFERENCE_PUFFIN_REFINE`, then `puffin_refine`, then [`DEFAULT`]. An empty variable is unset.
pub fn enabled(env: Option<&str>, settings: &toml::Table) -> bool {
    if let Some(value) = env.map(str::trim).filter(|value| !value.is_empty()) {
        return matches!(value.to_lowercase().as_str(), "1" | "true" | "yes" | "on");
    }
    settings.get(KEY).and_then(toml::Value::as_bool).unwrap_or(DEFAULT)
}

/// Whether refine mode is on for this process.
pub fn enabled_now() -> bool {
    enabled(std::env::var(ENV).ok().as_deref(), &settings())
}

/// Where the setting in force comes from, for `puffin refine`.
pub fn source_now() -> String {
    if std::env::var(ENV).is_ok_and(|value| !value.trim().is_empty()) {
        return format!("{ENV} (or {FLAG_ON}/{FLAG_OFF})");
    }
    match crate::config_file() {
        Some(path) if settings().contains_key(KEY) => format!("{KEY} in {}", path.display()),
        _ => "the default".to_string(),
    }
}

/// The last `--refine` or `--no-refine` before a `--`, if any.
pub fn flag_setting(args: &[OsString]) -> Option<bool> {
    let mut setting = None;
    for arg in args.iter().skip(1) {
        match arg.to_str() {
            Some("--") => break,
            Some(FLAG_ON) => setting = Some(true),
            Some(FLAG_OFF) => setting = Some(false),
            _ => {}
        }
    }
    setting
}

/// The command line without the switches, which Codex does not know.
pub fn without_flags(args: Vec<OsString>) -> Vec<OsString> {
    let mut out = Vec::with_capacity(args.len());
    let mut after_dashes = false;
    for (index, arg) in args.into_iter().enumerate() {
        if index > 0 && !after_dashes {
            match arg.to_str() {
                Some("--") => after_dashes = true,
                Some(FLAG_ON) | Some(FLAG_OFF) => continue,
                _ => {}
            }
        }
        out.push(arg);
    }
    out
}

/// Turns the command line's switch into [`ENV`] for this process and everything it starts.
///
/// Called from `home::use_puffin_home`, first thing in Codex's `main()`, before any other thread
/// exists: the only point at which setting a variable is sound.
pub fn export_flag() {
    let args: Vec<OsString> = std::env::args_os().collect();
    if let Some(on) = flag_setting(&args) {
        // SAFETY: see above; no other thread can be reading the environment yet.
        unsafe { std::env::set_var(ENV, if on { "on" } else { "off" }) };
    }
}

// -- `puffin exec` -----------------------------------------------------------------------------

/// Where `exec` and its prompt are on a command line, as indexes into the user's arguments.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExecPrompt {
    pub exec: usize,
    /// None when no prompt was given (Codex then reads stdin).
    pub prompt: Option<usize>,
}

/// Finds `exec` and its prompt. None for any other command, and for `exec resume`, `exec fork`
/// and `exec review`, which continue or review work rather than start a task.
pub fn exec_prompt(command: &Command, user_args: &[String]) -> Option<ExecPrompt> {
    let exec = crate::first_positional(command, user_args)?;
    let subcommand = command.find_subcommand(&user_args[exec])?;
    if subcommand.get_name() != "exec" {
        return None;
    }
    let prompt = crate::first_positional(subcommand, &user_args[exec + 1..]).map(|index| exec + 1 + index);
    if let Some(index) = prompt
        && subcommand.find_subcommand(&user_args[index]).is_some()
    {
        return None;
    }
    Some(ExecPrompt { exec, prompt })
}

/// Puts `text` where the prompt is, or after everything when there was none.
pub fn with_prompt(args: Vec<OsString>, position: &ExecPrompt, text: &str) -> Vec<OsString> {
    let mut args = args;
    match position.prompt {
        Some(index) if index + 1 < args.len() => args[index + 1] = text.into(),
        _ => args.push(text.into()),
    }
    args
}

fn has_full_access(user_args: &[String]) -> bool {
    user_args.iter().any(|arg| FULL_ACCESS_FLAGS.contains(&arg.as_str()))
}

/// `tokens` without the options the study step sets itself (see [`STUDY_DROPS_WITH_VALUE`]).
fn without_study_options(tokens: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut index = 0;
    while index < tokens.len() {
        let token = tokens[index].as_str();
        if STUDY_DROPS_WITH_VALUE.contains(&token) {
            index += 2;
            continue;
        }
        let joined = STUDY_DROPS_WITH_VALUE.iter().any(|option| {
            if option.starts_with("--") {
                token.strip_prefix(option).is_some_and(|rest| rest.starts_with('='))
            } else {
                !token.starts_with("--") && token.len() > option.len() && token.starts_with(option)
            }
        });
        if !joined && !STUDY_DROPS.contains(&token) {
            out.push(token.to_string());
        }
        index += 1;
    }
    out
}

/// The study step's command line, from the user's own (`original`, before the launcher added
/// anything, which the child launcher adds again): the same options, a read-only sandbox unless
/// the user chose Full Access, an ephemeral session (it is never resumed), its final message to
/// `out`, and the task on stdin (`-`), which also keeps a long task off the argument list.
pub fn study_args(command: &Command, original: &[OsString], out: &Path, skip_git_check: bool) -> Option<Vec<OsString>> {
    let user_args: Vec<String> = original.iter().skip(1).map(|arg| arg.to_string_lossy().into_owned()).collect();
    let position = exec_prompt(command, &user_args)?;
    let options_end = position.prompt.unwrap_or(user_args.len());
    let full_access = has_full_access(&user_args[..options_end]);
    let root = without_study_options(&user_args[..position.exec]);
    let exec_options = without_study_options(&user_args[position.exec + 1..options_end]);
    let given = |flag: &str| root.iter().chain(&exec_options).any(|arg| arg == flag);
    let mut args: Vec<OsString> = vec![original.first().cloned().unwrap_or_else(|| "puffin".into())];
    args.extend(root.iter().map(OsString::from));
    args.push("exec".into());
    if !given("--ephemeral") {
        args.push("--ephemeral".into());
    }
    if skip_git_check && !given("--skip-git-repo-check") {
        args.push("--skip-git-repo-check".into());
    }
    if !full_access {
        args.extend(["-s".into(), "read-only".into()]);
    }
    args.extend(["-o".into(), out.as_os_str().to_os_string()]);
    args.extend(exec_options.iter().map(OsString::from));
    args.push("-".into());
    Some(args)
}

/// What a study step produced.
#[derive(Debug, Default)]
pub struct Study {
    pub refined: String,
    pub exit: Option<i32>,
    pub elapsed: Duration,
}

/// Runs the study step: this binary with `args`, the task on stdin, `PUFFIN_REFINE_STEP=study`,
/// and refine mode off for it. Waits as long as it takes.
pub fn run_study(args: &[OsString], task: &str, out: &Path, stdout: Stdio, stderr: Stdio) -> std::io::Result<Study> {
    let started = Instant::now();
    let _ = std::fs::remove_file(out);
    let mut child = std::process::Command::new(std::env::current_exe()?)
        .args(args.iter().skip(1))
        .env(STEP_ENV, STUDY)
        .env(ENV, "off")
        .stdin(Stdio::piped())
        .stdout(stdout)
        .stderr(stderr)
        .spawn()?;
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(task.as_bytes());
    }
    let status = child.wait()?;
    let refined = std::fs::read_to_string(out).unwrap_or_default().trim().to_string();
    Ok(Study { refined, exit: status.code(), elapsed: started.elapsed() })
}

/// A digest of a git work tree's state (status with untracked files, and both diffs); None
/// outside a repository. Compared before and after the study step, which is told to change nothing.
pub fn tree_fingerprint(dir: &Path) -> Option<String> {
    let git = |args: &[&str]| {
        std::process::Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(args)
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .ok()
            .filter(|output| output.status.success())
            .map(|output| output.stdout)
    };
    let mut hasher = Sha256::new();
    hasher.update(git(&["status", "--porcelain=v1", "-z", "--untracked-files=all"])?);
    hasher.update(git(&["diff", "--no-ext-diff", "--binary"]).unwrap_or_default());
    hasher.update(git(&["diff", "--cached", "--no-ext-diff", "--binary"]).unwrap_or_default());
    Some(hasher.finalize().iter().map(|byte| format!("{byte:02x}")).collect())
}

/// Reads what Codex would take as the prompt from stdin: all of it for `-` or no prompt, and for
/// a given prompt a piped stdin appended as Codex appends it. None when there is nothing to read
/// (Codex then says so itself).
fn task_text(user_args: &[String], position: &ExecPrompt) -> Option<String> {
    let given = position.prompt.map(|index| user_args[index].clone()).filter(|prompt| prompt != "-");
    let piped = !std::io::stdin().is_terminal();
    let mut stdin = String::new();
    if piped {
        let mut bytes = Vec::new();
        let _ = std::io::stdin().read_to_end(&mut bytes);
        stdin = String::from_utf8_lossy(&bytes).into_owned();
    }
    match given {
        Some(prompt) if stdin.trim().is_empty() => Some(prompt),
        Some(prompt) => {
            let close = if stdin.ends_with('\n') { "" } else { "\n" };
            Some(format!("{prompt}\n\n<stdin>\n{stdin}{close}</stdin>"))
        }
        None => (!stdin.trim().is_empty()).then_some(stdin),
    }
}

fn minutes(elapsed: Duration) -> String {
    let seconds = elapsed.as_secs();
    if seconds >= 60 { format!("{} min {} s", seconds / 60, seconds % 60) } else { format!("{seconds} s") }
}

/// `puffin exec` in refine mode. `original` is the user's command line, `prepared` the one the
/// launcher made of it, and `code_index` whether the session has the code index's tools.
///
/// - In the study step (`PUFFIN_REFINE_STEP=study`): the task on stdin becomes the study prompt.
/// - With refine mode on: runs the study step, then returns `prepared` with the task followed by
///   the rules and the description as its prompt. The study step's output goes to stderr, so
///   stdout carries the doing step's alone, as without refine mode.
/// - Otherwise, and for anything but a new `exec` task: `prepared` unchanged.
pub fn around_exec(command: &Command, original: &[OsString], prepared: Vec<OsString>, code_index: bool) -> Vec<OsString> {
    let user_args: Vec<String> = prepared.iter().skip(1).map(|arg| arg.to_string_lossy().into_owned()).collect();
    let Some(position) = exec_prompt(command, &user_args) else { return prepared };
    if std::env::var(STEP_ENV).is_ok_and(|step| step == STUDY) {
        let Some(task) = task_text(&user_args, &position) else { return prepared };
        let writes = if has_full_access(&user_args) { UNSANDBOXED_WRITES } else { READ_ONLY_WRITES };
        return with_prompt(prepared, &position, &study_prompt(&task, writes, code_index));
    }
    if !enabled_now() {
        return prepared;
    }
    let Some(task) = task_text(&user_args, &position) else { return prepared };
    let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |since| since.as_nanos());
    let out = std::env::temp_dir().join(format!("puffin-refine-{}-{stamp}.md", std::process::id()));
    let Some(study) = study_args(command, original, &out, false) else { return with_prompt(prepared, &position, &task) };
    let dir = crate::code_index::session_dir(&user_args);
    let before = tree_fingerprint(&dir);
    eprintln!("refine: studying the task first, in a separate read-only session ({}); then a fresh session does it.", source_now());
    // The study step's stdout goes to this process's stderr: stdout is the doing step's alone.
    let stdout = std::io::stderr().as_fd().try_clone_to_owned().map_or_else(|_| Stdio::inherit(), Stdio::from);
    let result = run_study(&study, &task, &out, stdout, Stdio::inherit());
    let _ = std::fs::remove_file(&out);
    let study = match result {
        Ok(study) => study,
        Err(error) => {
            eprintln!("refine: the study step could not start ({error}); doing the task without it.");
            return with_prompt(prepared, &position, &task);
        }
    };
    if before.is_some() && tree_fingerprint(&dir) != before {
        eprintln!("refine: the study step changed files in {}; they are kept, and the next step sees them.", dir.display());
    }
    if study.refined.is_empty() {
        let exit = study.exit.map_or("killed".to_string(), |code| format!("exit {code}"));
        eprintln!("refine: the study step wrote no description ({exit}, {}); doing the task without one.", minutes(study.elapsed));
        return with_prompt(prepared, &position, &task);
    }
    eprintln!(
        "refine: the study took {} and wrote a {}-character description; the session that does the task starts now.",
        minutes(study.elapsed),
        study.refined.chars().count()
    );
    with_prompt(prepared, &position, &fix_prompt(&task, &study.refined))
}

// -- the interactive session -------------------------------------------------------------------

/// Registers the interactive session's hook when refine mode is on and removes it when it is off.
/// Only interactive launches touch it: a `puffin exec` that ran with the mode off would otherwise
/// take the hook away from a session started with it on. A `hooks.json` beside `config.toml`
/// means the user keeps hooks there, and nothing is written (said once, at start).
pub fn register_hook(codex_home: &Path, interactive: bool) {
    if !interactive {
        return;
    }
    let on = enabled_now();
    if on {
        crate::notice::say(&format!(
            "refine: on ({}): the first task of each session is studied by a separate read-only session first, then done here.",
            source_now()
        ));
    }
    if on && codex_home.join("hooks.json").is_file() {
        crate::notice::say(
            "refine: on, but your hooks are in hooks.json, so the interactive session cannot be refined; use `puffin exec`, or move them to config.toml.",
        );
    }
    let _ = crate::compaction::register_session_hook(&codex_home.join("config.toml"), &REFINE_HOOK, "hook", on);
}

/// Why the hook lets a prompt through, or the task to study.
#[derive(Debug, PartialEq, Eq)]
pub enum HookPlan {
    Pass(&'static str),
    Study { session: String, cwd: PathBuf, task: String },
}

/// Decides what the hook does with Codex's input (`input`) and the session's transcript.
///
/// Only the first prompt of an interactive (`cli`) thread is a new task: a later prompt continues
/// the conversation, `puffin exec` refines in the launcher, a subagent works for its parent, and
/// the study step itself is never refined.
pub fn hook_plan(input: &Value, transcript: Option<&str>, step: Option<&str>, on: bool) -> HookPlan {
    if step.is_some_and(|step| !step.is_empty()) {
        return HookPlan::Pass("inside a study step");
    }
    if !on {
        return HookPlan::Pass("refine mode is off");
    }
    if input.get("agent_id").is_some_and(|agent| !agent.is_null()) {
        return HookPlan::Pass("a subagent");
    }
    let Some(transcript) = transcript else { return HookPlan::Pass("no transcript") };
    if !first_interactive_prompt(transcript) {
        return HookPlan::Pass("not the first prompt of an interactive thread");
    }
    let task = input.get("prompt").and_then(Value::as_str).unwrap_or_default().trim().to_string();
    if task.is_empty() {
        return HookPlan::Pass("an empty prompt");
    }
    let session = input.get("session_id").and_then(Value::as_str).unwrap_or_default();
    // A session id names files below; anything but a UUID's characters is refused.
    if session.is_empty() || !session.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return HookPlan::Pass("no session id");
    }
    let cwd = input.get("cwd").and_then(Value::as_str).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
    HookPlan::Study { session: session.to_string(), cwd, task }
}

/// Whether a transcript is an interactive thread's with no prompt yet: its `session_meta` says
/// `source: "cli"`, and no user message has been recorded (the prompt being submitted is recorded
/// after the hook).
pub fn first_interactive_prompt(transcript: &str) -> bool {
    let mut interactive = false;
    for line in transcript.lines() {
        let Ok(entry) = serde_json::from_str::<Value>(line) else { continue };
        let payload = &entry["payload"];
        match entry["type"].as_str() {
            Some("session_meta") => interactive = payload["source"].as_str() == Some("cli"),
            Some("event_msg") => {
                let user_message = payload["type"].as_str() == Some("user_message")
                    || (payload["type"].as_str() == Some("item_completed")
                        && payload["item"]["type"].as_str() == Some("UserMessage"));
                if user_message {
                    return false;
                }
            }
            _ => {}
        }
    }
    interactive
}

/// The hook's answer: the rules and the description as context for the model, and a line for the
/// person. With no description there is no context, and the turn goes on as without refine mode.
pub fn hook_answer(study: &Study, log: &Path) -> Value {
    if study.refined.is_empty() {
        let exit = study.exit.map_or("killed".to_string(), |code| format!("exit {code}"));
        return json!({
            "systemMessage": format!(
                "Refine: the study step wrote no description ({exit}, {}); working on the task without one. Log: {}",
                minutes(study.elapsed),
                log.display()
            ),
        });
    }
    json!({
        "systemMessage": format!(
            "Refine: studied the task first ({}); its {}-character description is in this session's context. Log: {}",
            minutes(study.elapsed),
            study.refined.chars().count(),
            log.display()
        ),
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": fix_block(&study.refined),
        },
    })
}

/// `puffin refine hook`: Codex's `UserPromptSubmit` hook (see the module). Reads Codex's input on
/// stdin, runs the study step when the prompt is a new interactive task, and prints the answer.
/// It never fails the turn: anything unexpected lets the prompt through as it is.
fn hook() -> i32 {
    let mut input = String::new();
    let _ = std::io::stdin().read_to_string(&mut input);
    let input: Value = serde_json::from_str(&input).unwrap_or(Value::Null);
    let transcript = input
        .get("transcript_path")
        .and_then(Value::as_str)
        .and_then(|path| std::fs::read_to_string(path).ok());
    let step = std::env::var(STEP_ENV).ok();
    let HookPlan::Study { session, cwd, task } = hook_plan(&input, transcript.as_deref(), step.as_deref(), enabled_now())
    else {
        return 0;
    };
    let Ok(codex_home) = codex_utils_home_dir::find_codex_home() else { return 0 };
    let dir = codex_home.as_path().join("refine");
    if std::fs::create_dir_all(&dir).is_err() {
        return 0;
    }
    prune(&dir);
    let out = dir.join(format!("{session}.md"));
    let log_path = dir.join(format!("{session}.log"));
    let program: OsString = std::env::current_exe().map(OsString::from).unwrap_or_else(|_| "puffin".into());
    let mut args: Vec<OsString> = vec![program, "exec".into(), "--ephemeral".into(), "--skip-git-repo-check".into()];
    args.extend(["-s".into(), "read-only".into(), "-C".into(), cwd.into_os_string()]);
    args.extend(["-o".into(), out.as_os_str().to_os_string(), "-".into()]);
    let (stdout, stderr) = match std::fs::File::create(&log_path).and_then(|log| Ok((log.try_clone()?, log))) {
        Ok((first, second)) => (Stdio::from(first), Stdio::from(second)),
        Err(_) => (Stdio::null(), Stdio::null()),
    };
    // The hook's own stdout is Codex's channel for the answer: nothing else may be written to it.
    let Ok(study) = run_study(&args, &task, &out, stdout, stderr) else { return 0 };
    println!("{}", hook_answer(&study, &log_path));
    0
}

fn prune(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let now = std::time::SystemTime::now();
    for entry in entries.flatten() {
        let old = entry
            .metadata()
            .and_then(|meta| meta.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age > KEEP);
        if old {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// `puffin refine [status]` and `puffin refine hook`.
pub fn run_cli(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        Some("hook") => hook(),
        None | Some("status") => {
            let state = if enabled_now() { "on" } else { "off" };
            println!("refine: {state} (from {})", source_now());
            println!(
                "Each new task is first studied by a read-only session that writes a refined description; a fresh session then does it. \
                 Applies to `puffin exec`, the first prompt of each interactive session, and Night Shift. \
                 Set it with {FLAG_ON}/{FLAG_OFF}, {ENV}=on|off, or {KEY} = true|false in dreamference.toml."
            );
            0
        }
        Some(other) => {
            eprintln!("puffin refine: unknown action `{other}` (use `puffin refine` to see the setting)");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(words: &[&str]) -> Vec<String> {
        words.iter().map(|word| word.to_string()).collect()
    }

    fn os(words: &[&str]) -> Vec<OsString> {
        words.iter().map(OsString::from).collect()
    }

    /// A root command shaped like Codex's: `-c` and `-s` take values, and `exec` has its own
    /// options and the subcommands that continue a session.
    fn codex_like() -> Command {
        let mut command = Command::new("codex")
            .arg(clap::Arg::new("config").short('c').long("config").num_args(1).global(true))
            .arg(clap::Arg::new("model").short('m').long("model").num_args(1))
            .arg(clap::Arg::new("sandbox").short('s').long("sandbox").num_args(1))
            .arg(clap::Arg::new("yolo").long("dangerously-bypass-approvals-and-sandbox").alias("yolo").num_args(0))
            .arg(clap::Arg::new("prompt"))
            .subcommand(
                Command::new("exec")
                    .alias("e")
                    .arg(clap::Arg::new("json").long("json").num_args(0))
                    .arg(clap::Arg::new("out").short('o').long("output-last-message").num_args(1))
                    .arg(clap::Arg::new("cd").short('C').long("cd").num_args(1))
                    .arg(clap::Arg::new("sandbox").short('s').long("sandbox").num_args(1))
                    .arg(clap::Arg::new("ephemeral").long("ephemeral").num_args(0))
                    .arg(clap::Arg::new("prompt"))
                    .subcommand(Command::new("resume").arg(clap::Arg::new("id")))
                    .subcommand(Command::new("review")),
            )
            .subcommand(Command::new("resume"));
        command.build();
        command
    }

    #[test]
    fn off_unless_switched_on_and_the_variable_wins() {
        let table = |text: &str| text.parse::<toml::Table>().unwrap_or_default();
        assert!(!enabled(None, &table("")));
        assert!(enabled(None, &table("puffin_refine = true")));
        assert!(!enabled(Some("off"), &table("puffin_refine = true")));
        assert!(enabled(Some("on"), &table("puffin_refine = false")));
        assert!(enabled(Some(" 1 "), &table("")));
        assert!(!enabled(Some(""), &table("")));
        assert!(!DEFAULT, "refine mode stays off until the 100-task pair decides");
    }

    #[test]
    fn the_flags_are_read_before_a_double_dash_and_taken_off_the_command_line() {
        assert_eq!(flag_setting(&os(&["puffin", "--refine", "exec", "x"])), Some(true));
        assert_eq!(flag_setting(&os(&["puffin", "--refine", "--no-refine"])), Some(false));
        assert_eq!(flag_setting(&os(&["puffin", "exec", "--", "--refine"])), None);
        assert_eq!(flag_setting(&os(&["--refine"])), None, "argv[0] is never a flag");
        assert_eq!(
            without_flags(os(&["puffin", "--refine", "exec", "--no-refine", "x", "--", "--refine"])),
            os(&["puffin", "exec", "x", "--", "--refine"])
        );
    }

    #[test]
    fn the_exec_prompt_is_found_after_options_and_continuations_are_left_alone() {
        let command = codex_like();
        let found = |words: &[&str]| exec_prompt(&command, &strings(words));
        assert_eq!(found(&["exec", "fix it"]), Some(ExecPrompt { exec: 0, prompt: Some(1) }));
        assert_eq!(found(&["-c", "x=1", "exec", "-o", "f", "--json", "fix it"]), Some(ExecPrompt { exec: 2, prompt: Some(6) }));
        assert_eq!(found(&["e", "fix it"]), Some(ExecPrompt { exec: 0, prompt: Some(1) }));
        assert_eq!(found(&["exec"]), Some(ExecPrompt { exec: 0, prompt: None }));
        assert_eq!(found(&["exec", "resume", "--last"]), None);
        assert_eq!(found(&["exec", "review"]), None);
        assert_eq!(found(&["fix it"]), None, "the interactive session is the hook's");
        assert_eq!(found(&["resume"]), None);
    }

    #[test]
    fn the_study_step_runs_read_only_ephemeral_with_its_own_output_and_the_task_on_stdin() {
        let command = codex_like();
        let out = Path::new("/tmp/r.md");
        let args = study_args(
            &command,
            &os(&["puffin", "-c", "x=1", "-s", "workspace-write", "exec", "--json", "-o", "mine.txt", "-C", "repo", "fix it"]),
            out,
            false,
        );
        assert_eq!(
            args,
            Some(os(&["puffin", "-c", "x=1", "exec", "--ephemeral", "-s", "read-only", "-o", "/tmp/r.md", "-C", "repo", "-"]))
        );
        // Joined forms go too, and an ephemeral the user asked for is not doubled.
        let args = study_args(&command, &os(&["puffin", "exec", "--sandbox=danger-full-access", "-omine", "--ephemeral", "x"]), out, true);
        assert_eq!(
            args,
            Some(os(&["puffin", "exec", "--skip-git-repo-check", "-s", "read-only", "-o", "/tmp/r.md", "--ephemeral", "-"]))
        );
    }

    #[test]
    fn full_access_is_kept_for_the_study_step_with_no_sandbox_added() {
        let command = codex_like();
        let args = study_args(&command, &os(&["puffin", "--yolo", "exec", "x"]), Path::new("/o"), false);
        assert_eq!(args, Some(os(&["puffin", "--yolo", "exec", "--ephemeral", "-o", "/o", "-"])));
    }

    #[test]
    fn the_prompt_is_replaced_in_place_or_added_last() {
        let position = ExecPrompt { exec: 0, prompt: Some(2) };
        assert_eq!(with_prompt(os(&["p", "exec", "--json", "task"]), &position, "new"), os(&["p", "exec", "--json", "new"]));
        let position = ExecPrompt { exec: 0, prompt: None };
        assert_eq!(with_prompt(os(&["p", "exec"]), &position, "new"), os(&["p", "exec", "new"]));
    }

    #[test]
    fn every_piece_is_present_and_the_study_prompt_carries_the_six_sections() {
        for name in ["study-intro", "study-sections", "study-code-index", "study", "fix-rules", "refined-heading", "no-refined", "fix"] {
            assert!(!piece(name).is_empty(), "{name}");
        }
        assert_eq!(piece("nothing"), "");
        let prompt = study_prompt("Make `{writes}` work.", READ_ONLY_WRITES, false);
        assert!(prompt.starts_with("This is the first of two steps. Do not fix anything yet: study the task below"));
        assert!(prompt.contains("1. Intent:") && prompt.contains("6. Acceptance checks:"));
        assert!(prompt.contains(READ_ONLY_WRITES));
        assert!(!prompt.contains("code_callers"));
        assert!(prompt.ends_with("Task:\nMake `{writes}` work."), "a task's own braces stay as written: {prompt}");
        assert!(!prompt.contains("{subject}") && !prompt.contains("{sections}") && !prompt.contains("{code_index}"));
        assert!(study_prompt("x", UNSANDBOXED_WRITES, true).contains("`code_search` with the task's words"));
    }

    #[test]
    fn the_fix_prompt_is_the_task_then_the_rules_then_the_description() {
        let prompt = fix_prompt("Add a flag.", "1. Intent: a flag.");
        assert!(prompt.starts_with("Add a flag.\n\n- A first step studied the task"));
        assert!(prompt.contains("the task is authoritative: where the two disagree, follow the task."));
        assert!(prompt.ends_with("Refined description (written by the first step; it may be incomplete or wrong):\n1. Intent: a flag."));
        assert!(fix_block("  ").ends_with("(The first step wrote no description: work from the task alone.)"));
    }

    fn transcript(lines: &[Value]) -> String {
        lines.iter().map(Value::to_string).collect::<Vec<_>>().join("\n")
    }

    #[test]
    fn only_the_first_prompt_of_an_interactive_thread_is_studied() {
        let meta = |source: &str| json!({"type": "session_meta", "payload": {"id": "s", "source": source}});
        let context = json!({"type": "response_item", "payload": {"type": "message", "role": "user", "content": []}});
        let prompted = json!({"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "UserMessage"}}});
        let older = json!({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}});
        assert!(first_interactive_prompt(&transcript(&[meta("cli"), context.clone()])));
        assert!(!first_interactive_prompt(&transcript(&[meta("cli"), context, prompted])));
        assert!(!first_interactive_prompt(&transcript(&[meta("cli"), older])));
        assert!(!first_interactive_prompt(&transcript(&[meta("exec")])), "exec refines in the launcher");
        assert!(!first_interactive_prompt(&transcript(&[json!({"type": "session_meta", "payload": {"source": {"subagent": "x"}}})])));
        assert!(!first_interactive_prompt(""));
    }

    #[test]
    fn the_hook_studies_a_new_task_and_passes_everything_else() {
        let fresh = transcript(&[json!({"type": "session_meta", "payload": {"source": "cli"}})]);
        let input = json!({"session_id": "01a1-b2", "cwd": "/repo", "prompt": " Add a flag. ", "agent_id": null});
        assert_eq!(
            hook_plan(&input, Some(&fresh), None, true),
            HookPlan::Study { session: "01a1-b2".into(), cwd: PathBuf::from("/repo"), task: "Add a flag.".into() }
        );
        assert!(matches!(hook_plan(&input, Some(&fresh), None, false), HookPlan::Pass(_)));
        assert!(matches!(hook_plan(&input, Some(&fresh), Some(STUDY), true), HookPlan::Pass(_)));
        assert!(matches!(hook_plan(&input, None, None, true), HookPlan::Pass(_)));
        let subagent = json!({"session_id": "s", "prompt": "x", "agent_id": "a1"});
        assert!(matches!(hook_plan(&subagent, Some(&fresh), None, true), HookPlan::Pass(_)));
        let odd = json!({"session_id": "../../etc", "prompt": "x"});
        assert!(matches!(hook_plan(&odd, Some(&fresh), None, true), HookPlan::Pass(_)));
    }

    #[test]
    fn the_hook_answers_with_context_only_when_there_is_a_description() {
        let study = Study { refined: "1. Intent: x".into(), exit: Some(0), elapsed: Duration::from_secs(95) };
        let answer = hook_answer(&study, Path::new("/h/refine/s.log"));
        assert_eq!(answer["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit");
        assert_eq!(answer["hookSpecificOutput"]["additionalContext"], fix_block("1. Intent: x"));
        assert!(answer["systemMessage"].as_str().is_some_and(|line| line.contains("1 min 35 s")));
        let empty = hook_answer(&Study { exit: Some(1), ..Study::default() }, Path::new("/l"));
        assert!(empty.get("hookSpecificOutput").is_none());
    }

    #[test]
    fn the_hook_is_registered_under_user_prompt_submit_with_no_matcher_and_no_time_limit() {
        let path = Path::new("/h/config.toml");
        let command = "/opt/puffin refine hook";
        let updated = crate::compaction::with_session_hook("", path, command, &REFINE_HOOK, true).unwrap_or_default();
        let parsed: toml::Table = updated.parse().unwrap_or_default();
        let group = &parsed["hooks"]["UserPromptSubmit"][0];
        assert!(group.get("matcher").is_none(), "{updated}");
        let handler = &group["hooks"][0];
        assert_eq!(handler["command"].as_str(), Some(command));
        assert_eq!(handler["timeout"].as_integer(), Some(HOOK_TIMEOUT_S));
        assert_eq!(handler["additionalContextLimit"].as_integer(), Some(0));
        assert!(handler["statusMessage"].as_str().is_some());
        let key = "/h/config.toml:user_prompt_submit:0:0";
        assert_eq!(
            parsed["hooks"]["state"][key]["trusted_hash"].as_str(),
            Some(crate::compaction::hook_hash_for(command, &REFINE_HOOK).as_str())
        );
        // The ledger beside it keeps its own group, and switching refine off removes only this one.
        let both = crate::compaction::with_ledger_hook(&updated, path, "/opt/puffin ledger", true).unwrap_or_default();
        let off = crate::compaction::with_session_hook(&both, path, command, &REFINE_HOOK, false).unwrap_or_default();
        let parsed: toml::Table = off.parse().unwrap_or_default();
        assert!(parsed["hooks"].get("UserPromptSubmit").is_none(), "{off}");
        assert_eq!(parsed["hooks"]["SessionStart"].as_array().map(Vec::len), Some(1));
        assert_eq!(parsed["hooks"]["state"].as_table().map(toml::Table::len), Some(1));
    }

    /// The hash Codex computes for a hook is SHA-256 over the canonical JSON of the TOML form of
    /// `{event_name, matcher, hooks: [handler]}` (codex-rs/hooks/src/engine/discovery.rs). This
    /// builds that identity from Codex's own types and compares, so a field name, a skipped
    /// `None` or the event label going wrong fails here rather than leaving the hook untrusted.
    #[test]
    fn the_refine_hooks_trust_hash_is_the_one_codex_computes_from_its_own_types() {
        use codex_config::HookHandlerConfig;
        use codex_config::MatcherGroup;
        #[derive(serde::Serialize)]
        struct Identity {
            event_name: &'static str,
            #[serde(flatten)]
            group: MatcherGroup,
        }
        let codex_hash = |hook: &SessionHook, command: &str| {
            let handler = HookHandlerConfig::Command {
                command: command.to_string(),
                command_windows: None,
                timeout_sec: Some(hook.timeout as u64),
                r#async: false,
                status_message: (!hook.status_message.is_empty()).then(|| hook.status_message.to_string()),
                additional_context_limit: hook.context_limit.filter(|limit| *limit != 2_500).map(|limit| limit as usize),
            };
            let group = MatcherGroup {
                matcher: (!hook.matcher.is_empty()).then(|| hook.matcher.to_string()),
                hooks: vec![handler],
            };
            let label: &'static str = if hook.event == "UserPromptSubmit" { "user_prompt_submit" } else { "session_start" };
            let value = toml::Value::try_from(Identity { event_name: label, group }).unwrap_or(toml::Value::Boolean(false));
            codex_config::version_for_toml(&value)
        };
        for hook in [REFINE_HOOK, crate::compaction::LEDGER_HOOK, crate::notice::NOTICE_HOOK] {
            let command = "/opt/puffin x";
            assert_eq!(crate::compaction::hook_hash_for(command, &hook), codex_hash(&hook, command), "{}", hook.subcommand);
        }
    }
}
