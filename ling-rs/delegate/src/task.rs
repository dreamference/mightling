//! The `task` tool, classes T1 to T5: plan, execute, test
//! (`specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §4.2, §6.2, §6.3).
//!
//! The caller (a Claude agent) hands over a spec: a linked worktree, the files that may change,
//! the steps and ONE acceptance test. The server runs the local `ling exec` in that worktree, then
//! checks the result itself, never trusting the agent: no commit, no file outside the allowed list
//! (reverted, and the task fails), no test file outside T2, the class's size limit, the acceptance
//! test, and ruff on touched Python. It returns facts (a bounded diff, the files, the acceptance
//! tail) and never commits. Every task is appended to a ledger, without content, and the
//! reviewer's verdict is appended later, so a class can be gated on its measured acceptance.

use std::io::Write;
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::exec::{
    RunSpec, Runner, StatusEntry, check_relative, delegate_ling_bin, git, ignored_paths,
    is_build_artifact, linked_worktree, main_checkout, parse_numstat, sandbox_check, sandbox_env,
    sandboxed, status_entries, tail_of, under_submodule, venv_tool,
};
use crate::tools::{RunTestRequest, RunTestResult, run_test, test_command};

/// The classes `task` serves.
pub const TASK_CLASSES: &[&str] = &["T1", "T2", "T3", "T4", "T5"];
/// Verdicts the coordinator can record.
pub const VERDICTS: &[&str] = &["accept", "reject", "reverted", "shadow_miss"];
/// Seconds `ling exec` may run, default and maximum.
pub const DEFAULT_AGENT_TIMEOUT_S: u64 = 1_500;
pub const MAX_AGENT_TIMEOUT_S: u64 = 3_600;
/// Characters of diff returned at most (about 5,000 tokens, under the spec's 8,000); a larger
/// diff fails the task as `too_large` and is saved to `diff.patch` in the task's folder (§4.2).
pub const MAX_DIFF_CHARS: usize = 20_000;
/// Characters of the agent's last message returned at most.
pub const MAX_NOTE_CHARS: usize = 1_500;
/// Acceptance rate a class needs, over at least [`MIN_DECIDED`] decided tasks.
pub const GATE_RATE: f64 = 0.95;
pub const MIN_DECIDED: usize = 20;
const MAX_FILES: usize = 20;
const MAX_STEPS: usize = 20;
const MAX_STEP_CHARS: usize = 1_000;

/// Where the plugin keeps its state and which model it talks to.
#[derive(Debug, Clone)]
pub struct TaskEnv {
    /// `$CODEX_HOME` (default `~/.mightling`).
    pub codex_home: PathBuf,
    pub max_parallel: usize,
    /// The node `ling exec` uses, passed as `MIGHTLING_NODE` (§5.1): a name or id, never an
    /// address. Unset, `ling` finds the model server through the launcher's own tiers.
    pub node: Option<String>,
    pub ling_bin: String,
}

impl TaskEnv {
    pub fn from_env() -> Self {
        let codex_home = std::env::var_os("CODEX_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".mightling")))
            .unwrap_or_else(|| PathBuf::from(".mightling"));
        let max_parallel = std::env::var("DREAMFERENCE_DELEGATE_MAX_PARALLEL")
            .ok()
            .and_then(|v| v.trim().parse::<f64>().ok())
            .map(|v| v as usize)
            .filter(|v| *v >= 1)
            .unwrap_or(2);
        let node = std::env::var("DREAMFERENCE_DELEGATE_NODE").ok().filter(|v| !v.trim().is_empty());
        let ling_bin = delegate_ling_bin();
        Self { codex_home, max_parallel, node, ling_bin }
    }

    fn state_dir(&self) -> PathBuf {
        self.codex_home.join("claude-plugin")
    }
}

/// The one acceptance test.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Acceptance {
    /// `pytest`, `cargo` or `npm`.
    pub runner: String,
    /// A pytest node id, a cargo test name or an npm test pattern.
    pub target: String,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub package: Option<String>,
}

/// A `task` call.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskRequest {
    /// `T1` to `T5`.
    pub class: String,
    /// A linked git worktree with no uncommitted change.
    pub worktree: String,
    /// The files that may change: exact relative paths, or folders ending in `/`.
    pub allowed_files: Vec<String>,
    pub steps: Vec<String>,
    pub acceptance: Acceptance,
    /// Seconds `ling exec` may run (default 1,500, at most 3,600).
    #[serde(default)]
    pub timeout_s: Option<u64>,
}

/// One gate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Gate {
    pub name: String,
    pub ok: bool,
    pub tail: String,
}

/// What `ling exec` did.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AgentRun {
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub seconds: u64,
    /// The agent's last message: a hint, never a fact.
    pub note: String,
}

/// The structured answer.
#[derive(Debug, Clone, Serialize)]
pub struct TaskResult {
    pub id: String,
    pub class: String,
    pub pass: bool,
    /// `ok`, or why it failed: `agent`, `committed`, `scope`, `test_files`, `weakened_tests`,
    /// `too_large`, `acceptance`, `gate`, `empty`.
    pub reason: String,
    /// The allowed files that changed.
    pub files: Vec<String>,
    /// Out-of-scope changes the server reverted.
    pub reverted: Vec<String>,
    pub changed_lines: usize,
    pub diff: String,
    /// The diff was over `MAX_DIFF_CHARS`: `diff` is empty and a note names the file holding it.
    pub diff_truncated: bool,
    pub acceptance: Option<RunTestResult>,
    pub gates: Vec<Gate>,
    pub agent: Option<AgentRun>,
    pub seconds: u64,
    pub notes: Vec<String>,
}

// ---------------------------------------------------------------------------------------------
// Validation and policy
// ---------------------------------------------------------------------------------------------

/// The changed-line limit of a class (`§6.2`, item 6).
pub fn size_limit(class: &str) -> usize {
    if class == "T1" || class == "T3" { 400 } else { 150 }
}

/// True for a path that is a test file by the usual conventions.
pub fn is_test_path(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    let mut parts: Vec<&str> = lower.split('/').collect();
    let name = parts.pop().unwrap_or("");
    parts.iter().any(|c| matches!(*c, "tests" | "test" | "__tests__" | "e2e"))
        || name.starts_with("test_")
        || name.contains("_test.")
        || name.contains(".test.")
        || name.contains(".spec.")
}

fn in_scope(path: &str, allowed: &[String]) -> bool {
    allowed.iter().any(|a| if a.ends_with('/') { path.starts_with(a.as_str()) } else { path == a })
}

fn validate(req: &TaskRequest) -> Result<(PathBuf, RunTestRequest), String> {
    if !TASK_CLASSES.contains(&req.class.as_str()) {
        return Err(format!("unknown class {:?}; the classes are {}", req.class, TASK_CLASSES.join(", ")));
    }
    if req.allowed_files.is_empty() || req.allowed_files.len() > MAX_FILES {
        return Err(format!("`allowed_files` lists 1 to {MAX_FILES} files"));
    }
    for f in &req.allowed_files {
        check_relative(f, "allowed file")?;
        if f.split('/').any(|c| c == ".git") {
            return Err("`.git` can never be an allowed file".to_string());
        }
    }
    if req.steps.is_empty() || req.steps.len() > MAX_STEPS {
        return Err(format!("`steps` lists 1 to {MAX_STEPS} steps"));
    }
    if req.steps.iter().any(|s| s.trim().is_empty() || s.chars().count() > MAX_STEP_CHARS) {
        return Err(format!("each step is 1 to {MAX_STEP_CHARS} characters"));
    }
    let worktree = linked_worktree(&req.worktree)?;
    for f in req.allowed_files.iter().chain(req.acceptance.cwd.iter()) {
        if let Some(sub) = under_submodule(&worktree, f) {
            return Err(format!("{f} is inside the submodule {sub}, which a task never changes or builds in"));
        }
    }
    let accept = RunTestRequest {
        runner: req.acceptance.runner.clone(),
        target: req.acceptance.target.clone(),
        worktree: worktree.display().to_string(),
        cwd: req.acceptance.cwd.clone(),
        package: req.acceptance.package.clone(),
        timeout_s: Some(900),
        lines: Some(60),
    };
    test_command(&accept)?;
    Ok((worktree, accept))
}

/// The prompt `ling exec` receives: the spec and the rules the server will check anyway.
pub fn build_prompt(req: &TaskRequest, accept: &RunTestRequest) -> String {
    let steps: Vec<String> = req.steps.iter().enumerate().map(|(i, s)| format!("{}. {}", i + 1, s.trim())).collect();
    let mut cmd = match accept.runner.as_str() {
        "pytest" => format!("python -m pytest {} -q -x", accept.target),
        "cargo" => format!("cargo test {}", accept.target),
        _ => format!("npm test -- {}", accept.target),
    };
    if let Some(p) = &accept.package {
        cmd = cmd.replacen("cargo test", &format!("cargo test -p {p}"), 1);
    }
    let cwd = accept.cwd.as_deref().map(|c| format!(" (from the folder `{c}`)")).unwrap_or_default();
    let tests = if req.class == "T2" {
        "You may edit tests, but never remove or weaken an assertion and never add a skip, xfail or ignore."
    } else {
        "Do not edit any test file."
    };
    format!(
        "You are working in a git worktree. Make exactly the change below and nothing else.\n\n\
         STEPS\n{}\n\n\
         FILES YOU MAY CHANGE (no others; a change anywhere else is reverted and the task fails)\n{}\n\n\
         DONE WHEN this command passes{cwd}: {cmd}\n\n\
         RULES\n- Run the command yourself and stop when it passes.\n- Do not commit, stash, reset, \
         switch branches or touch git state.\n- {tests}\n- Add no dependency and change no lockfile.\n\
         - If the steps cannot be done within the allowed files, stop and say why in your last message.",
        steps.join("\n"),
        req.allowed_files.iter().map(|f| format!("- {f}")).collect::<Vec<_>>().join("\n"),
    )
}

/// Reasons the T2 assertion rule rejects a diff: a removed assertion with no replacement, or an
/// added skip, xfail or ignore, in a test file. A heuristic, stated as such.
pub fn weakened_tests(diff: &str) -> Option<String> {
    let mut file = String::new();
    let (mut removed, mut added) = (0usize, 0usize);
    let mut flagged: Option<String> = None;
    let check = |file: &str, removed: usize, added: usize, flagged: &mut Option<String>| {
        if is_test_path(file) && removed > added && flagged.is_none() {
            *flagged = Some(format!("{file}: {} assertion line(s) removed, {added} added", removed));
        }
    };
    for line in diff.lines() {
        if let Some(rest) = line.strip_prefix("diff --git a/") {
            check(&file, removed, added, &mut flagged);
            file = rest.split(" b/").next().unwrap_or("").to_string();
            (removed, added) = (0, 0);
        } else if line.starts_with("---") || line.starts_with("+++") {
            continue;
        } else if let Some(l) = line.strip_prefix('-') {
            if l.contains("assert") {
                removed += 1;
            }
        } else if let Some(l) = line.strip_prefix('+') {
            if l.contains("assert") {
                added += 1;
            }
            let low = l.to_ascii_lowercase();
            if is_test_path(&file)
                && ["skip", "xfail", "#[ignore]", "xit(", "xdescribe("].iter().any(|m| low.contains(m))
                && flagged.is_none()
            {
                flagged = Some(format!("{file}: adds a skip, xfail or ignore"));
            }
        }
    }
    check(&file, removed, added, &mut flagged);
    flagged
}

// ---------------------------------------------------------------------------------------------
// The ledger
// ---------------------------------------------------------------------------------------------

/// The append-only ledger at `$CODEX_HOME/claude-plugin/ledger.jsonl`. No content: no brief text,
/// no code, no answer.
pub struct Ledger {
    path: PathBuf,
}

/// Counts for one class.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct ClassStats {
    pub class: String,
    /// Tasks that ran to a verdict of the server (pass or fail).
    pub tasks: usize,
    /// Rejected by the server's checks.
    pub rejected_verification: usize,
    /// Passed the checks, no reviewer verdict yet.
    pub pending: usize,
    pub accepted: usize,
    pub rejected_review: usize,
    pub reverted: usize,
    pub shadow_miss: usize,
    /// `accepted / (tasks - pending)`; `None` before any task is decided.
    pub acceptance: Option<f64>,
    /// At least 20 decided, acceptance >= 95%, no revert, no shadow miss.
    pub gate_met: bool,
}

fn fnv(text: &str) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in text.bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    format!("{h:016x}")
}

fn now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}

impl Ledger {
    pub fn new(env: &TaskEnv) -> Self {
        Self { path: env.state_dir().join("ledger.jsonl") }
    }

    pub fn append(&self, record: &Value) -> Result<(), String> {
        if let Some(dir) = self.path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("ledger: {e}"))?;
        }
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|e| format!("ledger: {e}"))?;
        writeln!(file, "{record}").map_err(|e| format!("ledger: {e}"))
    }

    fn records(&self) -> Vec<Value> {
        std::fs::read_to_string(&self.path)
            .unwrap_or_default()
            .lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect()
    }

    /// The class of a task that passed the server's checks, if the id is one.
    fn passed_class(&self, id: &str) -> Option<String> {
        self.records().iter().find_map(|r| {
            (r["kind"] == "task" && r["id"] == id && r["status"] == "pass")
                .then(|| r["class"].as_str().unwrap_or("").to_string())
        })
    }

    /// Per-class statistics; the latest verdict of a task counts.
    pub fn stats(&self) -> Vec<ClassStats> {
        let records = self.records();
        let mut out = Vec::new();
        for class in TASK_CLASSES {
            let mut s = ClassStats { class: class.to_string(), ..Default::default() };
            for t in records.iter().filter(|r| r["kind"] == "task" && r["class"] == *class) {
                match t["status"].as_str() {
                    Some("fail") => {
                        s.tasks += 1;
                        s.rejected_verification += 1;
                    }
                    Some("pass") => {
                        s.tasks += 1;
                        let verdict = records
                            .iter()
                            .filter(|r| r["kind"] == "verdict" && r["id"] == t["id"])
                            .last()
                            .and_then(|r| r["verdict"].as_str());
                        match verdict {
                            Some("accept") => s.accepted += 1,
                            Some("reject") => s.rejected_review += 1,
                            Some("reverted") => s.reverted += 1,
                            Some("shadow_miss") => s.shadow_miss += 1,
                            _ => s.pending += 1,
                        }
                    }
                    _ => {}
                }
            }
            let decided = s.tasks - s.pending;
            if decided > 0 {
                s.acceptance = Some(s.accepted as f64 / decided as f64);
            }
            s.gate_met = decided >= MIN_DECIDED
                && s.acceptance.unwrap_or(0.0) >= GATE_RATE
                && s.reverted == 0
                && s.shadow_miss == 0;
            out.push(s);
        }
        out
    }
}

/// Records the reviewer's verdict on a task that passed the server's checks.
pub fn record_verdict(env: &TaskEnv, id: &str, verdict: &str) -> Result<Value, String> {
    if !VERDICTS.contains(&verdict) {
        return Err(format!("unknown verdict {verdict:?}; the list is {}", VERDICTS.join(", ")));
    }
    if id.is_empty() || id.len() > 64 || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("invalid task id".to_string());
    }
    let ledger = Ledger::new(env);
    let class = ledger
        .passed_class(id)
        .ok_or_else(|| format!("no task {id} that passed the server's checks is in the ledger"))?;
    ledger.append(&json!({"kind": "verdict", "id": id, "ts": now(), "class": class, "verdict": verdict}))?;
    let stats = ledger.stats().into_iter().find(|s| s.class == class);
    Ok(json!({"id": id, "class": class, "verdict": verdict, "stats": stats}))
}

// ---------------------------------------------------------------------------------------------
// Running a task
// ---------------------------------------------------------------------------------------------

/// A slot among the `max_parallel` running tasks, released on drop.
struct Slot {
    path: PathBuf,
}

impl Drop for Slot {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

fn alive(pid: u32) -> bool {
    Path::new(&format!("/proc/{pid}")).exists() || !cfg!(target_os = "linux")
}

fn take_slot(env: &TaskEnv, id: &str) -> Result<Slot, String> {
    let dir = env.state_dir().join("running");
    std::fs::create_dir_all(&dir).map_err(|e| format!("state folder: {e}"))?;
    let mut running = 0;
    for entry in std::fs::read_dir(&dir).map_err(|e| e.to_string())?.flatten() {
        let pid = std::fs::read_to_string(entry.path()).ok().and_then(|t| t.trim().parse::<u32>().ok());
        match pid {
            Some(p) if alive(p) => running += 1,
            _ => {
                let _ = std::fs::remove_file(entry.path());
            }
        }
    }
    if running >= env.max_parallel {
        return Err(format!("busy: {running} task(s) running, the limit is {}", env.max_parallel));
    }
    let path = dir.join(format!("{id}.pid"));
    std::fs::write(&path, std::process::id().to_string()).map_err(|e| e.to_string())?;
    Ok(Slot { path })
}

/// Refuses while a SWE-bench run is on: a `ling-swe-*` unit is active (the model gate refuses
/// then anyway; this saves the wait).
fn benchmark_running(runner: &dyn Runner) -> Result<Option<String>, String> {
    for scope in [Some("--user"), None] {
        let mut argv = vec!["systemctl"];
        argv.extend(scope);
        argv.extend(["list-units", "ling-swe-*", "--all", "--state=active,activating", "--no-legend", "--plain"]);
        // A machine without systemd has no such unit.
        let Ok(out) = runner.run(&RunSpec::new(&argv, Path::new("/"), 15)) else { continue };
        if out.exit != Some(0) {
            return Err("cannot verify that no benchmark is running (systemctl failed)".to_string());
        }
        if let Some(line) = out.output.lines().find(|l| l.trim_start().starts_with("ling-swe-")) {
            return Ok(Some(line.split_whitespace().next().unwrap_or("").to_string()));
        }
    }
    Ok(None)
}

fn safe_rel(path: &str) -> bool {
    let p = Path::new(path);
    !p.is_absolute() && !p.components().any(|c| matches!(c, Component::ParentDir))
}

/// Puts a path back as it is at HEAD: restores a tracked file, deletes a new one.
fn revert(runner: &dyn Runner, wt: &Path, e: &StatusEntry) -> Result<(), String> {
    if !safe_rel(&e.path) {
        return Err(format!("unsafe path {}", e.path));
    }
    match e.code {
        '?' | 'A' | 'R' => {
            let _ = git(runner, wt, &["rm", "-f", "--cached", "--ignore-unmatch", "--", &e.path]);
            let full = wt.join(&e.path);
            match std::fs::symlink_metadata(&full) {
                Ok(m) if m.is_dir() => std::fs::remove_dir_all(&full).map_err(|er| er.to_string()),
                Ok(_) => std::fs::remove_file(&full).map_err(|er| er.to_string()),
                Err(_) => Ok(()),
            }
        }
        _ => git(runner, wt, &["checkout", "HEAD", "--", &e.path]).map(|_| ()),
    }
}

fn fail(mut r: TaskResult, reason: &str, note: &str) -> TaskResult {
    r.pass = false;
    r.reason = reason.to_string();
    r.notes.push(note.to_string());
    r
}

/// Runs one task. `Err` is a refusal before anything ran (nothing is logged as a delegation but
/// the refusal itself); `Ok` carries the verdict of the server's checks, pass or fail.
pub fn execute(req: &TaskRequest, runner: &dyn Runner, env: &TaskEnv) -> Result<TaskResult, String> {
    let (wt, accept) = validate(req)?;
    let ledger = Ledger::new(env);
    let started = std::time::Instant::now();
    let id = format!("t{}-{}", chrono::Utc::now().format("%y%m%d%H%M%S"), std::process::id());
    let repo_hash = fnv(&main_checkout(&wt).unwrap_or_else(|| wt.clone()).display().to_string());
    let refuse = |why: String| -> Result<TaskResult, String> {
        let _ = ledger.append(&json!({"kind": "task", "id": id, "ts": now(), "class": req.class,
            "repo": repo_hash, "status": "refused", "reason": why}));
        Err(why)
    };

    match benchmark_running(runner) {
        Ok(Some(unit)) => return refuse(format!("a benchmark run is active ({unit}); the model is not available for tasks now")),
        Ok(None) => {}
        Err(e) => return refuse(e),
    }
    let _slot = match take_slot(env, &id) {
        Ok(s) => s,
        Err(e) => return refuse(e),
    };
    match status_entries(runner, &wt) {
        Ok(entries) if entries.is_empty() => {}
        Ok(entries) => {
            let names: Vec<String> = entries.iter().take(5).map(|e| e.path.clone()).collect();
            return refuse(format!("the worktree has uncommitted changes ({}); commit or stash them first, so the task's diff is only the model's", names.join(", ")));
        }
        Err(e) => return refuse(e),
    }
    let base = match git(runner, &wt, &["rev-parse", "HEAD"]) {
        Ok(h) => h.trim().to_string(),
        Err(e) => return refuse(e),
    };
    // The acceptance test and the gates run the model's code: only inside the sandbox. Checked
    // before the model spends its time.
    if let Err(e) = sandbox_check(runner, &env.ling_bin, &wt) {
        return refuse(e);
    }
    // Ignored paths now, so a file the model hides behind a `.gitignore` rule is still seen.
    let ignored_before = match ignored_paths(runner, &wt) {
        Ok(v) => v,
        Err(e) => return refuse(e),
    };

    let mut r = TaskResult {
        id: id.clone(),
        class: req.class.clone(),
        pass: false,
        reason: String::new(),
        files: vec![],
        reverted: vec![],
        changed_lines: 0,
        diff: String::new(),
        diff_truncated: false,
        acceptance: None,
        gates: vec![],
        agent: None,
        seconds: 0,
        notes: vec![],
    };
    let finish = |mut r: TaskResult| -> TaskResult {
        r.seconds = started.elapsed().as_secs();
        r
    };
    // A failure of the server's own git or test calls after the model ran is still a logged task.
    macro_rules! tri {
        ($e:expr) => {
            match $e {
                Ok(v) => v,
                Err(e) => {
                    let msg = format!("server error: {e}");
                    return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, "server", &msg))));
                }
            }
        };
    }

    // 1. The model works.
    let task_dir = env.state_dir().join("tasks").join(&id);
    let _ = std::fs::create_dir_all(&task_dir);
    let last_message = task_dir.join("last_message.txt");
    let prompt = build_prompt(req, &accept);
    let wt_str = wt.display().to_string();
    let argv = vec![
        env.ling_bin.clone(),
        "exec".into(),
        "-C".into(),
        wt_str,
        "-s".into(),
        "workspace-write".into(),
        "--skip-git-repo-check".into(),
        "-o".into(),
        last_message.display().to_string(),
        prompt,
    ];
    let agent_timeout = req.timeout_s.unwrap_or(DEFAULT_AGENT_TIMEOUT_S).clamp(60, MAX_AGENT_TIMEOUT_S);
    let spec = RunSpec {
        argv,
        cwd: wt.clone(),
        env: node_env(env),
        timeout_s: agent_timeout,
        batch: true,
    };
    let ran = runner.run(&spec);
    let note = std::fs::read_to_string(&last_message)
        .map(|t| tail_of(&t, 40, MAX_NOTE_CHARS).0)
        .unwrap_or_default();
    let agent = match &ran {
        Ok(o) => AgentRun { exit_code: o.exit, timed_out: o.timed_out, seconds: o.elapsed_s, note },
        Err(e) => AgentRun { exit_code: None, timed_out: false, seconds: 0, note: e.clone() },
    };
    let agent_ok = matches!(&ran, Ok(o) if o.exit == Some(0) && !o.timed_out);
    r.agent = Some(agent);

    // 2. The server looks at what changed, whatever the agent says.
    match git(runner, &wt, &["rev-parse", "HEAD"]) {
        Ok(h) if h.trim() != base => {
            let _ = git(runner, &wt, &["reset", "--mixed", &base]);
            r.notes.push("the agent moved HEAD (a commit); undone, the changes kept as working-tree changes".to_string());
            r.reason = "committed".to_string();
        }
        _ => {}
    }
    let entries = tri!(status_entries(runner, &wt));
    let mut touched: Vec<String> = Vec::new();
    let mut violations: Vec<String> = Vec::new();
    for e in &entries {
        let out_of_scope = !in_scope(&e.path, &req.allowed_files);
        let test_file = req.class != "T2" && is_test_path(&e.path);
        if out_of_scope || test_file {
            tri!(revert(runner, &wt, e));
            r.reverted.push(e.path.clone());
            if out_of_scope {
                violations.push("scope".to_string());
            } else {
                violations.push("test_files".to_string());
            }
        } else {
            touched.push(e.path.clone());
        }
    }
    // New ignored files (a `conftest.py` behind a `.gitignore` rule) are invisible to the status
    // above and to the diff; anything but build output is the model's and goes.
    for path in tri!(ignored_paths(runner, &wt)) {
        if ignored_before.contains(&path) || is_build_artifact(&path) {
            continue;
        }
        tri!(revert(runner, &wt, &StatusEntry { path: path.trim_end_matches('/').to_string(), code: '?' }));
        r.notes.push(format!("{path} is new and ignored by a .gitignore rule, so not in the diff; removed"));
        r.reverted.push(path);
        violations.push("scope".to_string());
    }
    r.files = touched.clone();

    // 3. The diff of what stays (new files made visible without staging their content).
    let new_files: Vec<&str> = entries
        .iter()
        .filter(|e| e.code == '?' && touched.contains(&e.path))
        .map(|e| e.path.as_str())
        .collect();
    if !new_files.is_empty() {
        let mut args = vec!["add", "-N", "--"];
        args.extend(new_files);
        tri!(git(runner, &wt, &args));
    }
    if !touched.is_empty() {
        let mut args = vec!["diff", "--no-color", "--no-renames", "HEAD", "--"];
        args.extend(touched.iter().map(String::as_str));
        let diff = tri!(git(runner, &wt, &args));
        let mut nargs = vec!["diff", "--numstat", "--no-renames", "HEAD", "--"];
        nargs.extend(touched.iter().map(String::as_str));
        r.changed_lines =
            parse_numstat(&tri!(git(runner, &wt, &nargs))).iter().map(|n| n.added + n.deleted).sum();
        if diff.chars().count() > MAX_DIFF_CHARS {
            tri!(std::fs::write(task_dir.join("diff.patch"), &diff).map_err(|e| e.to_string()));
            r.diff_truncated = true;
        } else {
            r.diff = diff.clone();
        }
        if req.class == "T2" {
            if let Some(why) = weakened_tests(&diff) {
                violations.push("weakened_tests".to_string());
                r.notes.push(format!("assertion rule: {why}"));
            }
        }
    }

    // 4. Verdict of the checks, in the order that is cheapest to know.
    if !r.reverted.is_empty() {
        let n = r.reverted.len();
        r.notes.push(format!("{n} change(s) outside the allowed files or in test files were reverted"));
    }
    if r.reason == "committed" {
        return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, "committed", "a task never commits"))));
    }
    if !agent_ok {
        let why = r.agent.as_ref().map(|a| if a.timed_out { "ling exec timed out".to_string() } else { format!("ling exec failed ({:?})", a.exit_code) }).unwrap_or_default();
        return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, "agent", &why))));
    }
    if let Some(v) = violations.first() {
        let v = v.clone();
        return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, &v, "rejected by the policy rules (§6.2)"))));
    }
    if touched.is_empty() {
        return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, "empty", "the agent changed no allowed file"))));
    }
    let limit = size_limit(&req.class);
    if r.changed_lines > limit {
        let note = format!("{} changed lines, the limit for {} is {limit}", r.changed_lines, req.class);
        return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, "too_large", &note))));
    }
    if r.diff_truncated {
        let note = format!(
            "the diff is over {MAX_DIFF_CHARS} characters; it is not returned, the full diff is at {}",
            task_dir.join("diff.patch").display()
        );
        return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, "too_large", &note))));
    }

    // 5. The acceptance test, then the cheap gates.
    let acc = tri!(run_test(&accept, runner, &env.ling_bin));
    let acc_ok = acc.passed;
    r.acceptance = Some(acc);
    if !acc_ok {
        return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, "acceptance", "the acceptance test does not pass"))));
    }
    let py: Vec<&str> = touched
        .iter()
        .filter(|p| p.ends_with(".py") && wt.join(p).is_file())
        .map(String::as_str)
        .collect();
    if !py.is_empty() {
        let ruff = venv_tool(&wt, "ruff");
        for (name, sub) in [("ruff format --check", "format"), ("ruff check", "check")] {
            let mut argv = vec![ruff.clone(), sub.to_string()];
            if sub == "format" {
                argv.push("--check".to_string());
            }
            argv.extend(py.iter().map(|p| p.to_string()));
            // ruff reads the worktree's own config: the sandbox too.
            let spec = RunSpec {
                argv: sandboxed(&env.ling_bin, &wt, &wt, &argv),
                cwd: wt.clone(),
                env: sandbox_env(),
                timeout_s: 120,
                batch: false,
            };
            let out = tri!(runner.run(&spec));
            let ok = out.exit == Some(0) && !out.timed_out;
            r.gates.push(Gate { name: name.to_string(), ok, tail: tail_of(&out.output, 15, 1500).0 });
        }
        if r.gates.iter().any(|g| !g.ok) {
            return Ok(log_and_return(&ledger, &repo_hash, env, finish(fail(r, "gate", "a gate fails on the touched Python files"))));
        }
    }
    r.pass = true;
    r.reason = "ok".to_string();
    r.notes.push("the diff is not applied or committed; review it, then record the verdict with task_verdict".to_string());
    Ok(log_and_return(&ledger, &repo_hash, env, finish(r)))
}

/// The environment `ling exec` gets beyond the server's own: `MIGHTLING_NODE` when the plugin's
/// `node` is set (§5.1), else nothing, so a client keeps the launcher's tiers and a node its loopback.
fn node_env(env: &TaskEnv) -> Vec<(String, String)> {
    env.node
        .iter()
        .map(|n| ("MIGHTLING_NODE".to_string(), n.clone()))
        .collect()
}

fn log_and_return(ledger: &Ledger, repo: &str, env: &TaskEnv, r: TaskResult) -> TaskResult {
    let _ = ledger.append(&json!({
        "kind": "task", "id": r.id, "ts": now(), "class": r.class, "repo": repo,
        "node": env.node, "status": if r.pass { "pass" } else { "fail" }, "reason": r.reason,
        "files": r.files.len(), "reverted": r.reverted.len(), "changed_lines": r.changed_lines,
        "acceptance_ok": r.acceptance.as_ref().map(|a| a.passed),
        "gates_ok": r.gates.iter().all(|g| g.ok),
        "model_s": r.agent.as_ref().map(|a| a.seconds), "total_s": r.seconds,
    }));
    r
}

/// The ledger table for `ling-delegate ledger`.
pub fn format_stats(stats: &[ClassStats]) -> String {
    let mut out = String::from("CLASS  TASKS  ACCEPTED  PENDING  REJ(verify)  REJ(review)  REVERTED  RATE    GATE\n");
    for s in stats {
        let rate = s.acceptance.map(|a| format!("{:.0}%", a * 100.0)).unwrap_or_else(|| "-".into());
        out.push_str(&format!(
            "{:<5}  {:<5}  {:<8}  {:<7}  {:<11}  {:<11}  {:<8}  {:<6}  {}\n",
            s.class, s.tasks, s.accepted, s.pending, s.rejected_verification, s.rejected_review,
            s.reverted, rate, if s.gate_met { "met" } else { "not met" }
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::exec::RunOutput;
    use std::cell::RefCell;

    /// A runner that plays git, the test runner, ruff, systemctl and `ling exec` against a real
    /// temp folder: `ling exec` writes the files the scenario says the model wrote.
    struct World {
        wt: PathBuf,
        /// (path, content) the "model" writes.
        writes: Vec<(&'static str, &'static str)>,
        status: RefCell<String>,
        swe_unit: bool,
        head: RefCell<Vec<String>>,
        accept_exit: i32,
        ruff_exit: i32,
        ling_exit: i32,
        log: RefCell<Vec<String>>,
        status_after: String,
        numstat: String,
        diff: String,
        /// `ling sandbox -- true` succeeds.
        sandbox_ok: bool,
        /// `git status --ignored` output, before and after the model.
        ignored: RefCell<String>,
        ignored_after: String,
        /// The extra environment `ling exec` got.
        exec_env: RefCell<Vec<(String, String)>>,
    }

    impl Runner for World {
        fn run(&self, spec: &RunSpec) -> Result<RunOutput, String> {
            let j = ling_safegit::plain_argv(&spec.argv).join(" ");
            self.log.borrow_mut().push(j.clone());
            let ok = |out: &str| Ok(RunOutput { exit: Some(0), output: out.to_string(), timed_out: false, elapsed_s: 2 });
            if j.starts_with("systemctl") {
                return if self.swe_unit && j.contains("--user") {
                    ok("ling-swe-run1.service loaded active running\n")
                } else {
                    ok("")
                };
            }
            if j.starts_with("ling sandbox") {
                if j.ends_with(" -- true") {
                    let exit = if self.sandbox_ok { 0 } else { 1 };
                    return Ok(RunOutput { exit: Some(exit), output: "bwrap: no".into(), timed_out: false, elapsed_s: 0 });
                }
                let (inner, start) = crate::tools::tests::unwrap_sandbox(&spec.argv).expect("a well-formed sandbox argv");
                assert!(Path::new(&start).starts_with(&self.wt), "{start}");
                let inner = inner.join(" ");
                if inner.contains("pytest") || inner.contains("cargo test") {
                    return Ok(RunOutput { exit: Some(self.accept_exit), output: "1 passed".into(), timed_out: false, elapsed_s: 3 });
                }
                if inner.contains("ruff") {
                    return Ok(RunOutput { exit: Some(self.ruff_exit), output: "ruff said".into(), timed_out: false, elapsed_s: 1 });
                }
                return Err(format!("unexpected in the sandbox: {inner}"));
            }
            if j.starts_with("ling exec ") {
                assert!(spec.batch, "the model runs in the batch slice");
                assert!(j.contains("-s workspace-write"));
                *self.exec_env.borrow_mut() = spec.env.clone();
                for (p, c) in &self.writes {
                    let full = self.wt.join(p);
                    std::fs::create_dir_all(full.parent().unwrap()).unwrap();
                    std::fs::write(full, c).unwrap();
                }
                *self.status.borrow_mut() = self.status_after.clone();
                *self.ignored.borrow_mut() = self.ignored_after.clone();
                return Ok(RunOutput { exit: Some(self.ling_exit), output: "done".into(), timed_out: false, elapsed_s: 30 });
            }
            if j.starts_with("git status") && j.contains("--ignored") {
                return ok(&self.ignored.borrow());
            }
            if j.starts_with("git status") {
                return ok(&self.status.borrow());
            }
            if j.starts_with("git rev-parse HEAD") {
                let mut h = self.head.borrow_mut();
                let v = if h.len() > 1 { h.remove(0) } else { h[0].clone() };
                return ok(&format!("{v}\n"));
            }
            if j.contains("--numstat") {
                return ok(&self.numstat);
            }
            if j.starts_with("git diff") {
                return ok(&self.diff);
            }
            if j.starts_with("git ") {
                return ok("");
            }
            assert!(!(j.contains("pytest") || j.contains("cargo") || j.contains("ruff")), "ran on the host: {j}");
            Err(format!("unexpected: {j}"))
        }
    }

    fn setup(tag: &str) -> (PathBuf, TaskEnv) {
        let base = std::env::temp_dir().join(format!("ling-task-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let wt = base.join("wt");
        std::fs::create_dir_all(wt.join("src")).unwrap();
        std::fs::write(wt.join(".git"), "gitdir: /nowhere/.git/worktrees/wt\n").unwrap();
        let env = TaskEnv {
            codex_home: base.join("home"),
            max_parallel: 2,
            node: None,
            ling_bin: "ling".into(),
        };
        (wt, env)
    }

    fn world(wt: &Path) -> World {
        World {
            wt: wt.to_path_buf(),
            writes: vec![("src/a.py", "x = 1\n")],
            status: RefCell::new(String::new()),
            swe_unit: false,
            head: RefCell::new(vec!["aaa".into()]),
            accept_exit: 0,
            ruff_exit: 0,
            ling_exit: 0,
            log: RefCell::new(vec![]),
            status_after: " M src/a.py\0".into(),
            numstat: "1\t1\tsrc/a.py\n".into(),
            diff: "diff --git a/src/a.py b/src/a.py\n-x = 0\n+x = 1\n".into(),
            sandbox_ok: true,
            ignored: RefCell::new("!! .mypy_cache/\0".into()),
            ignored_after: "!! .mypy_cache/\0".into(),
            exec_env: RefCell::new(vec![]),
        }
    }

    fn request(wt: &Path, class: &str) -> TaskRequest {
        TaskRequest {
            class: class.into(),
            worktree: wt.display().to_string(),
            allowed_files: vec!["src/a.py".into()],
            steps: vec!["Set x to 1.".into()],
            acceptance: Acceptance { runner: "pytest".into(), target: "tests/test_a.py::test_x".into(), ..Default::default() },
            timeout_s: None,
        }
    }

    #[test]
    fn a_good_task_passes_returns_the_diff_and_never_commits() {
        let (wt, env) = setup("good");
        let w = world(&wt);
        let r = execute(&request(&wt, "T3"), &w, &env).unwrap();
        assert!(r.pass, "{:?}", r.notes);
        assert_eq!(r.reason, "ok");
        assert_eq!(r.files, vec!["src/a.py"]);
        assert_eq!(r.changed_lines, 2);
        assert!(r.diff.contains("+x = 1"));
        assert!(r.acceptance.as_ref().unwrap().passed);
        assert_eq!(r.gates.len(), 2);
        let log = w.log.borrow().join("\n");
        assert!(!log.contains("git commit") && !log.contains("git add ."), "{log}");
        // The ledger holds the task, without content.
        let stats = Ledger::new(&env).stats();
        let t3 = stats.iter().find(|s| s.class == "T3").unwrap();
        assert_eq!((t3.tasks, t3.pending), (1, 1));
        let text = std::fs::read_to_string(env.state_dir().join("ledger.jsonl")).unwrap();
        assert!(!text.contains("Set x to 1") && !text.contains("x = 1"));
        assert!(!env.state_dir().join("running").join(format!("{}.pid", r.id)).exists(), "slot released");
    }

    #[test]
    fn the_plugin_node_reaches_ling_exec_as_mightling_node_and_never_as_an_address() {
        let (wt, mut env) = setup("node");
        let w = world(&wt);
        assert!(execute(&request(&wt, "T3"), &w, &env).unwrap().pass);
        assert!(
            w.exec_env.borrow().is_empty(),
            "unset: the launcher's tiers decide"
        );
        env.node = Some("node-a".into());
        let w = world(&wt);
        assert!(execute(&request(&wt, "T3"), &w, &env).unwrap().pass);
        assert_eq!(
            *w.exec_env.borrow(),
            vec![("MIGHTLING_NODE".to_string(), "node-a".to_string())]
        );
    }

    #[test]
    fn the_acceptance_test_and_the_gates_run_only_in_the_sandbox() {
        let (wt, env) = setup("sbx");
        let w = world(&wt);
        assert!(execute(&request(&wt, "T3"), &w, &env).unwrap().pass);
        let log = w.log.borrow();
        let tests: Vec<&String> =
            log.iter().filter(|c| !c.starts_with("ling exec") && (c.contains("pytest") || c.contains("ruff"))).collect();
        assert_eq!(tests.len(), 3, "{log:?}");
        assert!(tests.iter().all(|c| c.starts_with("ling sandbox -P :workspace --sandbox-state-disable-network -C ")), "{tests:?}");
    }

    #[test]
    fn a_sandbox_that_does_not_start_refuses_the_task_before_the_model_runs() {
        let (wt, env) = setup("nosbx");
        let mut w = world(&wt);
        w.sandbox_ok = false;
        let err = execute(&request(&wt, "T3"), &w, &env).unwrap_err();
        assert!(err.contains("sandbox does not start"), "{err}");
        let log = w.log.borrow();
        assert!(!log.iter().any(|c| c.starts_with("ling exec") || c.contains("pytest") || c.contains("ruff")), "{log:?}");
    }

    #[test]
    fn a_new_file_hidden_by_a_gitignore_rule_is_removed_and_fails_the_task() {
        let (wt, env) = setup("ignored");
        let mut w = world(&wt);
        w.writes = vec![("src/a.py", "x = 1\n"), ("src/conftest.py", "import os\n"), ("src/__pycache__/a.pyc", "x")];
        w.ignored_after = "!! .mypy_cache/\0!! src/__pycache__/\0!! src/conftest.py\0".into();
        let r = execute(&request(&wt, "T3"), &w, &env).unwrap();
        assert_eq!(r.reason, "scope");
        assert_eq!(r.reverted, vec!["src/conftest.py"]);
        assert!(!wt.join("src/conftest.py").exists());
        assert!(wt.join("src/__pycache__/a.pyc").exists(), "build output stays");
        assert!(r.acceptance.is_none(), "no test runs");
    }

    #[test]
    fn changes_outside_the_allowed_files_are_reverted_and_fail_the_task() {
        let (wt, env) = setup("scope");
        let mut w = world(&wt);
        w.writes = vec![("src/a.py", "x = 1\n"), ("src/other.py", "y\n")];
        w.status_after = " M src/a.py\0?? src/other.py\0".into();
        let r = execute(&request(&wt, "T3"), &w, &env).unwrap();
        assert!(!r.pass);
        assert_eq!(r.reason, "scope");
        assert_eq!(r.reverted, vec!["src/other.py"]);
        assert!(!wt.join("src/other.py").exists(), "the stray new file is gone");
        assert!(wt.join("src/a.py").exists(), "the allowed change stays for inspection");
        assert!(r.acceptance.is_none(), "no test is run on a rejected diff");
    }

    #[test]
    fn test_files_are_off_limits_outside_t2() {
        let (wt, env) = setup("tests");
        let mut w = world(&wt);
        w.writes = vec![("tests/test_a.py", "assert True\n")];
        w.status_after = "?? tests/test_a.py\0".into();
        let mut q = request(&wt, "T1");
        q.allowed_files = vec!["tests/".into()];
        let r = execute(&q, &w, &env).unwrap();
        assert_eq!(r.reason, "test_files");
        assert!(!wt.join("tests/test_a.py").exists());
        // In T2 the same file is allowed.
        let (wt2, env2) = setup("tests2");
        let mut w2 = world(&wt2);
        w2.writes = vec![("tests/test_a.py", "assert True\n")];
        w2.status_after = " M tests/test_a.py\0".into();
        w2.numstat = "1\t0\ttests/test_a.py\n".into();
        w2.diff = "diff --git a/tests/test_a.py b/tests/test_a.py\n+assert 1\n".into();
        let mut q2 = request(&wt2, "T2");
        q2.allowed_files = vec!["tests/".into()];
        assert!(execute(&q2, &w2, &env2).unwrap().pass);
    }

    #[test]
    fn t2_may_not_weaken_a_test() {
        let diff = "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/x\n+++ b/x\n-    assert f() == 2\n+    pass\n";
        assert!(weakened_tests(diff).unwrap().contains("assertion"));
        let skip = "diff --git a/tests/test_a.py b/tests/test_a.py\n+@pytest.mark.skip\n";
        assert!(weakened_tests(skip).unwrap().contains("skip"));
        let fine = "diff --git a/tests/test_a.py b/tests/test_a.py\n-    assert f() == 2\n+    assert f() == 3\n";
        assert!(weakened_tests(fine).is_none());
        let src = "diff --git a/src/a.py b/src/a.py\n-    assert x\n+    pass\n";
        assert!(weakened_tests(src).is_none(), "only test files are judged");
    }

    #[test]
    fn a_commit_by_the_agent_is_undone_and_fails() {
        let (wt, env) = setup("commit");
        let w = world(&wt);
        *w.head.borrow_mut() = vec!["aaa".into(), "bbb".into()];
        let r = execute(&request(&wt, "T3"), &w, &env).unwrap();
        assert_eq!(r.reason, "committed");
        assert!(w.log.borrow().iter().any(|c| c == "git reset --mixed aaa"));
    }

    #[test]
    fn size_limits_acceptance_and_gates_each_fail_the_task() {
        let (wt, env) = setup("size");
        let mut w = world(&wt);
        w.numstat = "100\t60\tsrc/a.py\n".into();
        assert_eq!(execute(&request(&wt, "T2"), &w, &env).unwrap().reason, "too_large");
        // A diff over the cap is refused with its path, not returned cut (§4.2).
        let (wt, env) = setup("bigdiff");
        let mut w = world(&wt);
        w.diff = format!(
            "diff --git a/src/a.py b/src/a.py\n{}",
            "+x = 1\n".repeat(MAX_DIFF_CHARS / 7 + 1)
        );
        let r = execute(&request(&wt, "T3"), &w, &env).unwrap();
        assert_eq!((r.pass, r.reason.as_str(), r.diff_truncated), (false, "too_large", true));
        assert!(r.diff.is_empty() && r.acceptance.is_none(), "refused before the acceptance test");
        let saved = env.state_dir().join("tasks").join(&r.id).join("diff.patch");
        assert_eq!(std::fs::read_to_string(&saved).unwrap(), w.diff);
        assert!(r.notes.iter().any(|n| n.contains(&saved.display().to_string())), "{:?}", r.notes);
        // 160 lines is under T3's 400.
        let (wt, env) = setup("acc");
        let mut w = world(&wt);
        w.accept_exit = 1;
        let r = execute(&request(&wt, "T3"), &w, &env).unwrap();
        assert_eq!(r.reason, "acceptance");
        assert!(!r.acceptance.unwrap().passed);
        let (wt, env) = setup("gate");
        let mut w = world(&wt);
        w.ruff_exit = 1;
        let r = execute(&request(&wt, "T1"), &w, &env).unwrap();
        assert_eq!(r.reason, "gate");
        assert!(r.gates.iter().any(|g| !g.ok));
        let (wt, env) = setup("agent");
        let mut w = world(&wt);
        w.ling_exit = 1;
        assert_eq!(execute(&request(&wt, "T3"), &w, &env).unwrap().reason, "agent");
        let (wt, env) = setup("empty");
        let mut w = world(&wt);
        w.status_after = String::new();
        assert_eq!(execute(&request(&wt, "T3"), &w, &env).unwrap().reason, "empty");
    }

    #[test]
    fn refuses_during_a_benchmark_a_dirty_tree_and_too_many_tasks() {
        let (wt, env) = setup("refuse");
        let mut w = world(&wt);
        w.swe_unit = true;
        let err = execute(&request(&wt, "T3"), &w, &env).unwrap_err();
        assert!(err.contains("benchmark"), "{err}");
        assert!(!w.log.borrow().iter().any(|c| c.contains(" exec ")), "the model never ran");

        let (wt, env) = setup("dirty");
        let w = world(&wt);
        *w.status.borrow_mut() = " M src/a.py\0".into();
        assert!(execute(&request(&wt, "T3"), &w, &env).unwrap_err().contains("uncommitted"));

        let (wt, mut env) = setup("busy");
        env.max_parallel = 1;
        let dir = env.state_dir().join("running");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("other.pid"), std::process::id().to_string()).unwrap();
        let w = world(&wt);
        assert!(execute(&request(&wt, "T3"), &w, &env).unwrap_err().contains("busy"));
        // Refusals are logged but are not delegations.
        let stats = Ledger::new(&env).stats();
        assert_eq!(stats.iter().map(|s| s.tasks).sum::<usize>(), 0);
    }

    #[test]
    fn a_git_failure_after_the_model_ran_is_still_logged() {
        struct Broken(World);
        impl Runner for Broken {
            fn run(&self, spec: &RunSpec) -> Result<RunOutput, String> {
                let j = ling_safegit::plain_argv(&spec.argv).join(" ");
                if j.starts_with("git diff") {
                    return Ok(RunOutput { exit: Some(128), output: "fatal".into(), timed_out: false, elapsed_s: 0 });
                }
                self.0.run(spec)
            }
        }
        let (wt, env) = setup("broken");
        let r = execute(&request(&wt, "T3"), &Broken(world(&wt)), &env).unwrap();
        assert_eq!(r.reason, "server");
        let t3 = Ledger::new(&env).stats().into_iter().find(|s| s.class == "T3").unwrap();
        assert_eq!(t3.rejected_verification, 1);
    }

    #[test]
    fn the_spec_is_validated_before_anything_runs() {
        let (wt, env) = setup("spec");
        let w = world(&wt);
        let mut q = request(&wt, "T9");
        assert!(execute(&q, &w, &env).is_err());
        q = request(&wt, "T3");
        q.allowed_files = vec!["../x".into()];
        assert!(execute(&q, &w, &env).is_err());
        q.allowed_files = vec![".git/config".into()];
        assert!(execute(&q, &w, &env).is_err());
        std::fs::write(wt.join(".gitmodules"), "[submodule \"codex\"]\n\tpath = codex\n").unwrap();
        q = request(&wt, "T3");
        q.allowed_files = vec!["codex/codex-rs/x.rs".into()];
        assert!(execute(&q, &w, &env).unwrap_err().contains("submodule"));
        q = request(&wt, "T3");
        q.steps.clear();
        assert!(execute(&q, &w, &env).is_err());
        q = request(&wt, "T3");
        q.acceptance.runner = "make".into();
        assert!(execute(&q, &w, &env).is_err());
        q = request(&wt, "T3");
        q.acceptance.target = "--help".into();
        assert!(execute(&q, &w, &env).is_err());
        assert!(w.log.borrow().is_empty(), "nothing started");
    }

    #[test]
    fn verdicts_feed_the_gate() {
        let (wt, env) = setup("verdict");
        let ledger = Ledger::new(&env);
        for i in 0..20 {
            ledger.append(&json!({"kind":"task","id":format!("t{i}"),"class":"T1","status":"pass"})).unwrap();
            record_verdict(&env, &format!("t{i}"), if i == 0 { "reject" } else { "accept" }).unwrap();
        }
        let _ = wt;
        let t1 = ledger.stats().into_iter().find(|s| s.class == "T1").unwrap();
        assert_eq!((t1.accepted, t1.rejected_review), (19, 1));
        assert!(t1.gate_met, "19 of 20 is exactly 95%");
    }

    #[test]
    fn verdicts_need_a_known_passed_task_and_a_listed_verdict() {
        let (_, env) = setup("verdict2");
        assert!(record_verdict(&env, "nope", "accept").is_err());
        Ledger::new(&env).append(&json!({"kind":"task","id":"t1","class":"T1","status":"fail"})).unwrap();
        assert!(record_verdict(&env, "t1", "accept").is_err(), "a failed task has nothing to review");
        Ledger::new(&env).append(&json!({"kind":"task","id":"t2","class":"T1","status":"pass"})).unwrap();
        assert!(record_verdict(&env, "t2", "maybe").is_err());
        assert!(record_verdict(&env, "t2", "accept").is_ok());
        // A later revert overrides the acceptance and breaks the gate.
        assert!(record_verdict(&env, "t2", "reverted").is_ok());
        let t1 = Ledger::new(&env).stats().into_iter().find(|s| s.class == "T1").unwrap();
        assert_eq!((t1.accepted, t1.reverted), (0, 1));
    }

    #[test]
    fn size_limits_and_test_paths() {
        assert_eq!((size_limit("T1"), size_limit("T3"), size_limit("T2"), size_limit("T5")), (400, 400, 150, 150));
        for p in ["tests/a.py", "src/test_a.py", "a_test.go", "ui/a.test.ts", "ui/a.spec.js", "x/e2e/y.ts"] {
            assert!(is_test_path(p), "{p}");
        }
        assert!(!is_test_path("src/contest.py") && !is_test_path("src/latest.rs"));
    }
}
