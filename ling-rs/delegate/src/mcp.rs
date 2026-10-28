//! MCP stdio server (`ling delegate mcp`, `specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §4.1, §4.2).
//!
//! Speaks JSON-RPC 2.0 over stdin/stdout (one JSON object per line, matching Claude Code's stdio
//! MCP transport and `ling-rs/apps/src/mcp.rs`). Exposed: the model-free `watch`, `lookup`, `run_test`,
//! `git_log` and `diff_stat`, and the model-backed `task` and `task_verdict` (probation, named in
//! the allow list). `ask` and `night` are refused with a clear message. `watch`, `task` and
//! `run_test` calls run on threads of their own so they never block the other tools.

use std::io::{self, BufRead, Write};
use std::sync::{Arc, Mutex};

use serde::Serialize;
use serde_json::{Value, json};

use crate::classes::{
    ALLOW_CLASSES_ENV, is_exact_tool_enabled, is_l1_enabled, is_task_enabled, is_w1_enabled,
};
use crate::exec::{Runner, SystemRunner};
use crate::lookup::{
    DEFAULT_LIMIT, IndexBackend, LOOKUP_OPS, LookupRequest, MAX_LIMIT, SystemIndex,
    execute as lookup_execute,
};
use crate::task::{
    MAX_AGENT_TIMEOUT_S, TASK_CLASSES, TaskEnv, TaskRequest, VERDICTS, execute as task_execute,
    record_verdict,
};
use crate::tools::{
    DEFAULT_LOG_LIMIT, DEFAULT_TAIL_LINES, DEFAULT_TEST_TIMEOUT_S, DiffStatRequest, GitLogRequest,
    MAX_LOG_LIMIT, MAX_TAIL_LINES, MAX_TEST_TIMEOUT_S, RunTestRequest, TEST_RUNNERS, diff_stat,
    git_log, run_test,
};
use crate::watch::{
    CLOSED_KINDS, DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, MIN_COMMAND_POLL_S, Probe, SystemProbe,
    WatchRequest, execute,
};

const PROTOCOL_VERSION: &str = "2025-06-18";
const SERVER_NAME: &str = "mightling";
const SERVER_VERSION: &str = "1.7.2";

/// What the tools reach: the code index, the process runner (git, tests, `ling exec`) and the
/// plugin's state. Injectable, so the tests need no network, no model and no toolchain.
pub struct Backends {
    pub index: Box<dyn IndexBackend + Send>,
    pub runner: Box<dyn Runner + Send>,
    pub env: TaskEnv,
}

impl Backends {
    pub fn system() -> Self {
        Self {
            index: Box::new(SystemIndex),
            runner: Box::new(SystemRunner),
            env: TaskEnv::from_env(),
        }
    }
}

/// Calls that can run for minutes. Each runs on a thread of its own, so a `task` or a `watch` in
/// flight never blocks `lookup` and the other tools of the same session.
fn is_slow_call(line: &str) -> bool {
    serde_json::from_str::<Value>(line)
        .map(|m| {
            m.get("method").and_then(Value::as_str) == Some("tools/call")
                && matches!(
                    m.pointer("/params/name").and_then(Value::as_str),
                    Some("watch" | "task" | "run_test")
                )
        })
        .unwrap_or(false)
}

/// Runs the MCP stdio loop over `stdin` and `stdout` until EOF.
pub fn serve_stdio() -> i32 {
    let allow_classes: Arc<Option<String>> = Arc::new(std::env::var(ALLOW_CLASSES_ENV).ok());
    let stdin = io::stdin();
    let writer = Arc::new(Mutex::new(io::stdout()));
    let mut inline_probe = SystemProbe::new();
    let inline_backends = Backends::system();
    let mut workers = Vec::new();

    let reply = |writer: &Arc<Mutex<io::Stdout>>, resp: String| -> io::Result<()> {
        let mut out = writer.lock().map_err(|_| io::Error::other("stdout lock"))?;
        writeln!(out, "{resp}")?;
        out.flush()
    };

    for line in stdin.lock().lines() {
        let Ok(line) = line else { return 1 };
        let trimmed = line.trim().to_string();
        if trimmed.is_empty() {
            continue;
        }
        if is_slow_call(&trimmed) {
            let (writer, allow) = (writer.clone(), allow_classes.clone());
            workers.push(std::thread::spawn(move || {
                let mut probe = SystemProbe::new();
                let backends = Backends::system();
                if let Some(resp) = handle_line(&trimmed, allow.as_deref(), &mut probe, &backends) {
                    let mut out = match writer.lock() {
                        Ok(o) => o,
                        Err(_) => return,
                    };
                    let _ = writeln!(out, "{resp}");
                    let _ = out.flush();
                }
            }));
        } else if let Some(resp) =
            handle_line(&trimmed, allow_classes.as_deref(), &mut inline_probe, &inline_backends)
        {
            if reply(&writer, resp).is_err() {
                return 1;
            }
        }
    }
    for w in workers {
        let _ = w.join();
    }
    0
}

/// Runs the MCP JSON-RPC loop over arbitrary `BufRead` / `Write` streams (used by tests and `serve_stdio`).
pub fn serve_stream<R: BufRead, W: Write>(
    reader: &mut R,
    writer: &mut W,
    allow_classes: Option<&str>,
    probe: &mut dyn Probe,
    backends: &Backends,
) -> io::Result<()> {
    let mut line = String::new();
    loop {
        line.clear();
        let n = reader.read_line(&mut line)?;
        if n == 0 {
            break;
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(resp) = handle_line(trimmed, allow_classes, probe, backends) {
            writeln!(writer, "{resp}")?;
            writer.flush()?;
        }
    }
    Ok(())
}

/// Handles a single JSON-RPC line and returns the serialized JSON-RPC response (or `None` for notifications).
pub fn handle_line(
    line: &str,
    allow_classes: Option<&str>,
    probe: &mut dyn Probe,
    backends: &Backends,
) -> Option<String> {
    let msg: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => {
            return Some(
                json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": { "code": -32700, "message": format!("parse error: {e}") }
                })
                .to_string(),
            );
        }
    };
    let id = msg.get("id").cloned();
    let method = msg.get("method").and_then(Value::as_str).unwrap_or("");
    let params = msg.get("params").cloned().unwrap_or(Value::Null);

    let result = match method {
        "initialize" => {
            let client_version = params
                .get("protocolVersion")
                .and_then(Value::as_str)
                .unwrap_or(PROTOCOL_VERSION);
            Ok(json!({
                "protocolVersion": client_version,
                "capabilities": { "tools": {} },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION
                }
            }))
        }
        "notifications/initialized" | "initialized" => return None,
        m if m.starts_with("notifications/") => return None,
        "ping" => Ok(json!({})),
        "tools/list" => {
            let mut tools = Vec::new();
            if is_l1_enabled(allow_classes) {
                tools.push(lookup_tool_definition());
            }
            if is_exact_tool_enabled(allow_classes, "run_test") {
                tools.push(run_test_tool_definition());
            }
            if is_exact_tool_enabled(allow_classes, "git_log") {
                tools.push(git_log_tool_definition());
            }
            if is_exact_tool_enabled(allow_classes, "diff_stat") {
                tools.push(diff_stat_tool_definition());
            }
            if TASK_CLASSES.iter().any(|c| is_task_enabled(allow_classes, c)) {
                tools.push(task_tool_definition());
                tools.push(task_verdict_tool_definition());
            }
            if is_w1_enabled(allow_classes) {
                tools.push(watch_tool_definition());
            }
            Ok(json!({ "tools": tools }))
        }
        "tools/call" => Ok(call_tool(&params, allow_classes, probe, backends)),
        _ => {
            id.as_ref()?;
            Err(json!({
                "code": -32601,
                "message": format!("method not found: {method}")
            }))
        }
    };

    let id = id?;
    Some(
        match result {
            Ok(r) => json!({ "jsonrpc": "2.0", "id": id, "result": r }),
            Err(e) => json!({ "jsonrpc": "2.0", "id": id, "error": e }),
        }
        .to_string(),
    )
}

/// JSON Schema definition of the `watch` tool advertised in `tools/list`.
pub fn watch_tool_definition() -> Value {
    json!({
        "name": "watch",
        "description": format!(
            "Wait for ONE local condition from a closed list ({}) and return once with {{met, observed, at}}. Uses no model. Use this instead of sleep loops or polling whenever a wait exceeds a minute (default timeout {}s, hard max {}s, command poll >= {}s).",
            CLOSED_KINDS.join(", "),
            DEFAULT_TIMEOUT_S,
            MAX_TIMEOUT_S,
            MIN_COMMAND_POLL_S
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": CLOSED_KINDS,
                    "description": "Condition kind from the closed list: file_line, pid_exit, http_status, gh_run, systemd_unit, command, night_task, git_ref, or timeout."
                },
                "target": {
                    "type": "string",
                    "description": "Target of the watch: file path (file_line), PID (pid_exit), loopback http:// URL (http_status), GitHub Actions run ID (gh_run), user unit name (systemd_unit), allowlisted command (command), Night Shift task ID (night_task), git ref name (git_ref), or duration in seconds (timeout)."
                },
                "pattern": {
                    "type": "string",
                    "description": "Substring or regex to match in a line (file_line, command), expected HTTP status (http_status), expected unit state (systemd_unit), or baseline commit (git_ref)."
                },
                "regex": {
                    "type": "boolean",
                    "description": "Treat `pattern` as a regular expression (default false)."
                },
                "path": {
                    "type": "string",
                    "description": "File path to watch (for kind = file_line)."
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID to wait on (for kind = pid_exit)."
                },
                "url": {
                    "type": "string",
                    "description": "Loopback http:// URL (for kind = http_status)."
                },
                "status": {
                    "type": "integer",
                    "description": "Expected HTTP status code (default 200, for kind = http_status)."
                },
                "run_id": {
                    "type": "string",
                    "description": "GitHub Actions run ID (for kind = gh_run)."
                },
                "repo": {
                    "type": "string",
                    "description": "Optional owner/repo slug for gh_run or repo path for git_ref."
                },
                "unit": {
                    "type": "string",
                    "description": "Systemd user unit name (for kind = systemd_unit)."
                },
                "state": {
                    "type": "string",
                    "description": "Expected systemd unit state (default 'active', for kind = systemd_unit)."
                },
                "command": {
                    "type": "string",
                    "description": "Allowlisted command to poll (git, gh run view, ling night show, ling-admin night status, systemctl --user is-active, curl to loopback)."
                },
                "exit_code": {
                    "type": "integer",
                    "description": "Expected exit code for kind = command (default 0 when pattern is omitted)."
                },
                "id": {
                    "type": "string",
                    "description": "Night Shift task ID under $CODEX_HOME/night/tasks/<id>.json (for kind = night_task)."
                },
                "ref": {
                    "type": "string",
                    "description": "Git ref name to watch for movement (for kind = git_ref)."
                },
                "from": {
                    "type": "string",
                    "description": "Baseline commit hash for git_ref (if omitted, the initial poll's hash is used)."
                },
                "timeout_s": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TIMEOUT_S,
                    "description": "Maximum seconds to wait before returning met: false (default 1800, hard max 14400)."
                },
                "poll_interval_s": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Poll interval in seconds (clamped to >= 15s for command, gh_run, systemd_unit, and git_ref)."
                }
            },
            "required": ["kind"],
            "additionalProperties": false
        }
    })
}

/// JSON Schema definition of the `lookup` tool advertised in `tools/list`.
pub fn lookup_tool_definition() -> Value {
    json!({
        "name": "lookup",
        "description": format!(
            "Exact answers from the code index with NO model: where a name is defined (def), every reference (refs), what calls it (callers), implementations (impl), or the definitions of a file (outline). Returns hits as {{path, start, end, quote}} with each quote read back from the file, at most {MAX_LIMIT} rows and about 1,500 tokens. Use it before reading files for a single symbol. Zero hits is a fact about the index only; for a literal string use rg."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "op": { "type": "string", "enum": LOOKUP_OPS, "description": "def, refs, callers, impl, or outline." },
                "name": { "type": "string", "description": "The name to look up (qualify it as Type.name or by path:line when ambiguous); for outline, the file path relative to the repository." },
                "scope": { "type": "string", "description": "Keep only rows under this relative path prefix or glob." },
                "repo": { "type": "string", "description": "Repository folder (default: the current directory)." },
                "limit": { "type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "description": format!("Rows to return (default {DEFAULT_LIMIT}).") }
            },
            "required": ["op", "name"],
            "additionalProperties": false
        }
    })
}

fn call_tool(
    params: &Value,
    allow_classes: Option<&str>,
    probe: &mut dyn Probe,
    backends: &Backends,
) -> Value {
    let name = params.get("name").and_then(Value::as_str).unwrap_or("");
    let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));

    match name {
        "watch" => {
            if !is_w1_enabled(allow_classes) {
                return tool_error(
                    "class W1 (`watch`) is disabled by DREAMFERENCE_DELEGATE_ALLOW_CLASSES",
                );
            }
            let req: WatchRequest = match serde_json::from_value(args) {
                Ok(r) => r,
                Err(e) => return tool_error(&format!("invalid watch arguments: {e}")),
            };
            match execute(&req, probe) {
                Ok(result) => {
                    let text = serde_json::to_string(&result).unwrap_or_default();
                    let structured = serde_json::to_value(&result).unwrap_or(Value::Null);
                    json!({
                        "content": [{ "type": "text", "text": text }],
                        "structuredContent": structured,
                        "isError": false
                    })
                }
                Err(err) => tool_error(&err.to_string()),
            }
        }
        "lookup" => {
            if !is_l1_enabled(allow_classes) {
                return tool_error(
                    "class L1 (`lookup`) is disabled by DREAMFERENCE_DELEGATE_ALLOW_CLASSES",
                );
            }
            let req: LookupRequest = match serde_json::from_value(args) {
                Ok(r) => r,
                Err(e) => return tool_error(&format!("invalid lookup arguments: {e}")),
            };
            match lookup_execute(&req, backends.index.as_ref()) {
                Ok(result) => {
                    let text = serde_json::to_string(&result).unwrap_or_default();
                    let structured = serde_json::to_value(&result).unwrap_or(Value::Null);
                    json!({
                        "content": [{ "type": "text", "text": text }],
                        "structuredContent": structured,
                        "isError": false
                    })
                }
                Err(err) => tool_error(&err),
            }
        }
        "run_test" | "git_log" | "diff_stat" => {
            if !is_exact_tool_enabled(allow_classes, name) {
                return tool_error(&format!(
                    "`{name}` is disabled by DREAMFERENCE_DELEGATE_ALLOW_CLASSES"
                ));
            }
            let runner = backends.runner.as_ref();
            match name {
                "run_test" => run_tool::<RunTestRequest, _, _>(args, name, |r| run_test(r, runner)),
                "git_log" => run_tool::<GitLogRequest, _, _>(args, name, |r| git_log(r, runner)),
                _ => run_tool::<DiffStatRequest, _, _>(args, name, |r| diff_stat(r, runner)),
            }
        }
        "task" => {
            let req: TaskRequest = match serde_json::from_value(args) {
                Ok(r) => r,
                Err(e) => return tool_error(&format!("invalid task arguments: {e}")),
            };
            if !is_task_enabled(allow_classes, &req.class) {
                return tool_error(&format!(
                    "class {} (`task`) is not enabled: add `task` or the class code to DREAMFERENCE_DELEGATE_ALLOW_CLASSES",
                    req.class
                ));
            }
            match task_execute(&req, backends.runner.as_ref(), &backends.env) {
                Ok(result) => ok_value(&result),
                Err(err) => tool_error(&format!("refused: {err}")),
            }
        }
        "task_verdict" => {
            if !TASK_CLASSES.iter().any(|c| is_task_enabled(allow_classes, c)) {
                return tool_error("`task` is not enabled by DREAMFERENCE_DELEGATE_ALLOW_CLASSES");
            }
            let id = args.get("id").and_then(Value::as_str).unwrap_or("");
            let verdict = args.get("verdict").and_then(Value::as_str).unwrap_or("");
            match record_verdict(&backends.env, id, verdict) {
                Ok(v) => ok_value(&v),
                Err(e) => tool_error(&e),
            }
        }
        "ask" | "night" => tool_error(&format!(
            "tool {name:?} is disabled: only `watch`, `lookup`, `run_test`, `git_log`, `diff_stat`, `task` and `task_verdict` are built"
        )),
        other => tool_error(&format!("unknown tool: {other}")),
    }
}

fn ok_value<T: Serialize>(result: &T) -> Value {
    let text = serde_json::to_string(result).unwrap_or_default();
    let structured = serde_json::to_value(result).unwrap_or(Value::Null);
    json!({
        "content": [{ "type": "text", "text": text }],
        "structuredContent": structured,
        "isError": false
    })
}

/// Parses `args` as `A`, runs `f`, and wraps the result.
fn run_tool<A, T, F>(args: Value, name: &str, f: F) -> Value
where
    A: serde::de::DeserializeOwned,
    T: Serialize,
    F: FnOnce(&A) -> Result<T, String>,
{
    let req: A = match serde_json::from_value(args) {
        Ok(r) => r,
        Err(e) => return tool_error(&format!("invalid {name} arguments: {e}")),
    };
    match f(&req) {
        Ok(r) => ok_value(&r),
        Err(e) => tool_error(&e),
    }
}

/// JSON Schema definition of the `run_test` tool.
pub fn run_test_tool_definition() -> Value {
    json!({
        "name": "run_test",
        "description": format!(
            "Run ONE named test in a linked git worktree with NO model and get a bounded result {{passed, exit_code, summary, tail}}. Runners: {}. Output is the last {DEFAULT_TAIL_LINES} lines (at most {MAX_TAIL_LINES}), default timeout {DEFAULT_TEST_TIMEOUT_S}s (at most {MAX_TEST_TIMEOUT_S}s), run at nice 10. Use it instead of running a suite and reading its log.",
            TEST_RUNNERS.join(", ")
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "runner": { "type": "string", "enum": TEST_RUNNERS, "description": "pytest, cargo or npm." },
                "target": { "type": "string", "description": "pytest: a node id (tests/test_x.py::test_y); cargo: a test name filter; npm: a test pattern. One token, never a flag." },
                "worktree": { "type": "string", "description": "Path of a linked git worktree (never the main checkout)." },
                "cwd": { "type": "string", "description": "Folder inside the worktree to run from (a crate or package), relative." },
                "package": { "type": "string", "description": "cargo only: the package (-p)." },
                "timeout_s": { "type": "integer", "minimum": 1, "maximum": MAX_TEST_TIMEOUT_S },
                "lines": { "type": "integer", "minimum": 1, "maximum": MAX_TAIL_LINES, "description": "Output lines to return." }
            },
            "required": ["runner", "target", "worktree"],
            "additionalProperties": false
        }
    })
}

/// JSON Schema definition of the `git_log` tool.
pub fn git_log_tool_definition() -> Value {
    json!({
        "name": "git_log",
        "description": format!(
            "The history of one file, or of a line range in it (a function: take start and end from `lookup`), with NO model: commits as {{hash, author, date, subject}}, at most {MAX_LOG_LIMIT} (default {DEFAULT_LOG_LIMIT}). The range is checked against the file's current length. Use it to learn why code is as it is before reading old diffs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": { "type": "string", "description": "The file, relative to the repository." },
                "start": { "type": "integer", "minimum": 1, "description": "First line of the range (with end)." },
                "end": { "type": "integer", "minimum": 1, "description": "Last line of the range." },
                "repo": { "type": "string", "description": "Repository or worktree root (default: the current directory)." },
                "limit": { "type": "integer", "minimum": 1, "maximum": MAX_LOG_LIMIT }
            },
            "required": ["path"],
            "additionalProperties": false
        }
    })
}

/// JSON Schema definition of the `diff_stat` tool.
pub fn diff_stat_tool_definition() -> Value {
    json!({
        "name": "diff_stat",
        "description": "The working tree's change summary against HEAD with NO model: branch, head, and per file {path, status, added, deleted}, plus totals; untracked files are listed by name. Bounded to 100 files. Use it instead of `git diff` to see how big a change is.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": { "type": "string", "description": "Repository or worktree root (default: the current directory)." }
            },
            "additionalProperties": false
        }
    })
}

/// JSON Schema definition of the `task` tool.
pub fn task_tool_definition() -> Value {
    json!({
        "name": "task",
        "description": format!(
            "Hand a BOUNDED edit with ONE clear test to the local Mightling model. It runs `ling exec` in your linked worktree (clean, no uncommitted change) restricted to `allowed_files`, then checks the result itself: a change outside the allowed files (or in a test file, outside T2) is reverted and fails the task, a commit is undone, the class's size limit applies, then the acceptance test and ruff on touched Python. Returns {{pass, reason, files, changed_lines, diff, acceptance, gates, agent}}. It NEVER commits and never applies anything: review the diff (give a reviewer only the brief and the diff), apply it yourself if good, then record the verdict with `task_verdict`. Takes 5 to 30 minutes (timeout up to {MAX_AGENT_TIMEOUT_S}s): call it from a background subagent. Classes: T1 lint/format/typing fixes, T2 test-only fixes, T3 mechanical refactors, T4 docs updates, T5 small UI items. Refused while a benchmark runs. Do not use it for design decisions, security-sensitive code or anything without a test."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "class": { "type": "string", "enum": TASK_CLASSES },
                "worktree": { "type": "string", "description": "Path of a clean linked git worktree (never the main checkout)." },
                "allowed_files": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 20, "description": "Relative file paths, or folders ending in `/`, that may change. Nothing else may." },
                "steps": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 20, "description": "What to do, in order, concretely." },
                "acceptance": {
                    "type": "object",
                    "description": "The ONE test that must pass.",
                    "properties": {
                        "runner": { "type": "string", "enum": TEST_RUNNERS },
                        "target": { "type": "string", "description": "pytest node id, cargo test name, or npm test pattern." },
                        "cwd": { "type": "string", "description": "Folder inside the worktree to run it from." },
                        "package": { "type": "string", "description": "cargo only: the package." }
                    },
                    "required": ["runner", "target"],
                    "additionalProperties": false
                },
                "timeout_s": { "type": "integer", "minimum": 60, "maximum": MAX_AGENT_TIMEOUT_S, "description": "Seconds the model may work (default 1500)." }
            },
            "required": ["class", "worktree", "allowed_files", "steps", "acceptance"],
            "additionalProperties": false
        }
    })
}

/// JSON Schema definition of the `task_verdict` tool.
pub fn task_verdict_tool_definition() -> Value {
    json!({
        "name": "task_verdict",
        "description": "Record the reviewer's verdict on a `task` that passed the server's checks, in the ledger that gates the class (95% accepted). accept: you applied the diff; reject: the review refused it; reverted: it was applied and later undone; shadow_miss: your own result differed in effect. No content is stored.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": { "type": "string", "description": "The task id `task` returned." },
                "verdict": { "type": "string", "enum": VERDICTS }
            },
            "required": ["id", "verdict"],
            "additionalProperties": false
        }
    })
}

fn tool_error(message: &str) -> Value {
    json!({
        "content": [{ "type": "text", "text": message }],
        "isError": true
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::watch::{CommandOutput, WatchResult};
    use std::path::Path;

    struct DummyProbe;

    struct NoIndex;

    struct NoRunner;

    impl Runner for NoRunner {
        fn run(&self, spec: &crate::exec::RunSpec) -> Result<crate::exec::RunOutput, String> {
            Err(format!("no process in tests: {}", spec.argv.join(" ")))
        }
    }

    fn be() -> Backends {
        Backends {
            index: Box::new(NoIndex),
            runner: Box::new(NoRunner),
            env: TaskEnv::from_env(),
        }
    }

    impl IndexBackend for NoIndex {
        fn run_index(&self, _repo: &Path, _args: &[String]) -> Result<String, String> {
            Ok(r#"{"rows":[{"path":"Cargo.toml","line":1,"detail":"x (to 2)"}],"notes":[]}"#.into())
        }
        fn read_lines(&self, _path: &Path) -> Result<Vec<String>, String> {
            Ok(vec!["[package]".into(), "name = \"x\"".into()])
        }
    }

    impl Probe for DummyProbe {
        fn now_s(&mut self) -> u64 {
            0
        }
        fn timestamp_rfc3339(&mut self) -> String {
            "2026-10-27T12:00:00Z".to_string()
        }
        fn sleep_s(&mut self, _seconds: u64) {}
        fn read_file_lines(&mut self, _path: &Path) -> Result<Vec<String>, String> {
            Ok(vec!["line 1".to_string(), "READY on port 8000".to_string()])
        }
        fn is_pid_alive(&mut self, _pid: i32) -> bool {
            false
        }
        fn http_status(
            &mut self,
            _host: &str,
            _port: u16,
            _request_path: &str,
            _url: &str,
        ) -> Result<u16, String> {
            Ok(200)
        }
        fn gh_run_view(
            &mut self,
            _run_id: &str,
            _repo: Option<&str>,
        ) -> Result<CommandOutput, String> {
            Ok(CommandOutput {
                exit_code: 0,
                stdout: r#"{"status":"completed","conclusion":"success"}"#.to_string(),
                stderr: String::new(),
            })
        }
        fn systemd_unit_state(&mut self, _unit: &str) -> Result<CommandOutput, String> {
            Ok(CommandOutput {
                exit_code: 0,
                stdout: "active\n".to_string(),
                stderr: String::new(),
            })
        }
        fn run_command(&mut self, _argv: &[String]) -> Result<CommandOutput, String> {
            Ok(CommandOutput {
                exit_code: 0,
                stdout: "ok\n".to_string(),
                stderr: String::new(),
            })
        }
        fn night_task_status(&mut self, _id: &str) -> Result<Option<String>, String> {
            Ok(Some("done".to_string()))
        }
        fn git_rev_parse(&mut self, _git_ref: &str, _repo: Option<&Path>) -> Result<String, String> {
            Ok("deadbeef".to_string())
        }
    }

    #[test]
    fn mcp_initialize_and_tools_list_expose_only_the_model_free_tools() {
        let mut probe = DummyProbe;
        let init_resp: Value = serde_json::from_str(
            &handle_line(
                r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}"#,
                None,
                &mut probe,
                &be(),
            )
            .unwrap(),
        )
        .unwrap();
        assert_eq!(init_resp["result"]["serverInfo"]["name"], "mightling");

        // Notifications return None.
        assert!(
            handle_line(
                r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#,
                None,
                &mut probe,
                &be(),
            )
            .is_none()
        );

        let list_resp: Value = serde_json::from_str(
            &handle_line(
                r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
                Some("W1"),
                &mut probe,
                &be(),
            )
            .unwrap(),
        )
        .unwrap();
        let tools = list_resp["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["name"], "watch");

        let both: Value = serde_json::from_str(
            &handle_line(r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#, None, &mut probe, &be())
                .unwrap(),
        )
        .unwrap();
        let names: Vec<&str> = both["result"]["tools"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        assert_eq!(names, vec!["lookup", "run_test", "git_log", "diff_stat", "watch"]);

        let with_task: Value = serde_json::from_str(
            &handle_line(
                r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
                Some("W1,L1,task"),
                &mut probe,
                &be(),
            )
            .unwrap(),
        )
        .unwrap();
        let names: Vec<&str> = with_task["result"]["tools"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        assert!(names.contains(&"task") && names.contains(&"task_verdict"));
    }

    #[test]
    fn mcp_lookup_answers_with_quotes_and_refuses_bad_arguments() {
        let mut probe = DummyProbe;
        let dir = std::env::current_dir().unwrap();
        let call = |args: Value, allow: Option<&str>, probe: &mut DummyProbe| -> Value {
            let line = json!({"jsonrpc":"2.0","id":9,"method":"tools/call",
                "params":{"name":"lookup","arguments":args}})
            .to_string();
            serde_json::from_str(&handle_line(&line, allow, probe, &be()).unwrap()).unwrap()
        };
        let ok = call(json!({"op":"def","name":"x","repo":dir}), None, &mut probe);
        assert_eq!(ok["result"]["isError"], false);
        assert_eq!(ok["result"]["structuredContent"]["hits"][0]["quote"], "[package]\nname = \"x\"");
        let bad = call(json!({"op":"rm","name":"x"}), None, &mut probe);
        assert_eq!(bad["result"]["isError"], true);
        let extra = call(json!({"op":"def","name":"x","shell":"ls"}), None, &mut probe);
        assert_eq!(extra["result"]["isError"], true);
        let off = call(json!({"op":"def","name":"x"}), Some("W1"), &mut probe);
        assert_eq!(off["result"]["isError"], true);
    }

    #[test]
    fn mcp_task_and_the_exact_tools_obey_the_allow_list_and_validate_arguments() {
        let mut probe = DummyProbe;
        let call = |name: &str, args: Value, allow: Option<&str>, probe: &mut DummyProbe| -> Value {
            let line = json!({"jsonrpc":"2.0","id":9,"method":"tools/call",
                "params":{"name":name,"arguments":args}})
            .to_string();
            serde_json::from_str(&handle_line(&line, allow, probe, &be()).unwrap()).unwrap()
        };
        let task_args = json!({"class":"T1","worktree":"/nonexistent","allowed_files":["a.py"],
            "steps":["x"],"acceptance":{"runner":"pytest","target":"t.py"}});
        // Not named in the allow list (and unset): refused before anything runs.
        let off = call("task", task_args.clone(), Some("W1,L1"), &mut probe);
        assert_eq!(off["result"]["isError"], true);
        assert!(off["result"]["content"][0]["text"].as_str().unwrap().contains("not enabled"));
        let unset = call("task", task_args.clone(), None, &mut probe);
        assert!(unset["result"]["content"][0]["text"].as_str().unwrap().contains("not enabled"));
        // Enabled: the bad worktree is refused by validation.
        let on = call("task", task_args, Some("task"), &mut probe);
        assert_eq!(on["result"]["isError"], true);
        assert!(on["result"]["content"][0]["text"].as_str().unwrap().contains("refused"));
        // Unknown fields are rejected.
        let extra = call("run_test", json!({"runner":"pytest","target":"t","worktree":"/x","shell":"ls"}), None, &mut probe);
        assert_eq!(extra["result"]["isError"], true);
        let bad = call("run_test", json!({"runner":"make","target":"t","worktree":"/x"}), None, &mut probe);
        assert_eq!(bad["result"]["isError"], true);
        let gated = call("git_log", json!({"path":"a"}), Some("W1"), &mut probe);
        assert!(gated["result"]["content"][0]["text"].as_str().unwrap().contains("disabled"));
        let verdict = call("task_verdict", json!({"id":"nope","verdict":"accept"}), Some("task"), &mut probe);
        assert_eq!(verdict["result"]["isError"], true);
    }

    #[test]
    fn slow_calls_are_recognised() {
        assert!(is_slow_call(r#"{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"task"}}"#));
        assert!(is_slow_call(r#"{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"watch"}}"#));
        assert!(!is_slow_call(r#"{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lookup"}}"#));
        assert!(!is_slow_call("not json"));
    }

    #[test]
    fn mcp_tools_call_executes_watch_and_refuses_other_classes() {
        let mut probe = DummyProbe;
        let call_resp: Value = serde_json::from_str(
            &handle_line(
                r#"{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"watch","arguments":{"kind":"file_line","target":"/tmp/out.log","pattern":"READY"}}}"#,
                Some("W1"),
                &mut probe,
                &be(),
            )
            .unwrap(),
        )
        .unwrap();
        assert_eq!(call_resp["result"]["isError"], false);
        let text = call_resp["result"]["content"][0]["text"].as_str().unwrap();
        let parsed: WatchResult = serde_json::from_str(text).unwrap();
        assert!(parsed.met);
        assert_eq!(parsed.observed, "/tmp/out.log:2: READY on port 8000");
        assert_eq!(parsed.at, "2026-10-27T12:00:00Z");

        // Disabled model-backed classes are refused.
        for disabled_tool in ["ask", "night"] {
            let req_line = json!({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": { "name": disabled_tool, "arguments": {} }
            })
            .to_string();
            let resp: Value =
                serde_json::from_str(&handle_line(&req_line, Some("W1"), &mut probe, &be()).unwrap())
                    .unwrap();
            assert_eq!(resp["result"]["isError"], true);
            assert!(
                resp["result"]["content"][0]["text"]
                    .as_str()
                    .unwrap()
                    .contains("is disabled")
            );
        }
    }
}
