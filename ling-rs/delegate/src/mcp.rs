//! MCP stdio server (`ling delegate mcp`, `specs/DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md` §4.1, §4.2).
//!
//! Speaks JSON-RPC 2.0 over stdin/stdout (one JSON object per line, matching Claude Code's stdio
//! MCP transport and `ling-rs/apps/src/mcp.rs`). In Phase P1, only class W1 (`watch`) is exposed;
//! calls to `ask`, `lookup`, `task`, or `night` are refused with a clear message.

use std::io::{self, BufRead, Write};

use serde_json::{Value, json};

use crate::classes::{ALLOW_CLASSES_ENV, is_w1_enabled};
use crate::watch::{
    CLOSED_KINDS, DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, MIN_COMMAND_POLL_S, Probe, SystemProbe,
    WatchRequest, execute,
};

const PROTOCOL_VERSION: &str = "2025-06-18";
const SERVER_NAME: &str = "mightling";
const SERVER_VERSION: &str = "1.7.1";

/// Runs the MCP stdio loop over `stdin` and `stdout` until EOF.
pub fn serve_stdio() -> i32 {
    let allow_classes = std::env::var(ALLOW_CLASSES_ENV).ok();
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut reader = stdin.lock();
    let mut writer = stdout.lock();
    let mut probe = SystemProbe::new();

    if serve_stream(&mut reader, &mut writer, allow_classes.as_deref(), &mut probe).is_err() {
        return 1;
    }
    0
}

/// Runs the MCP JSON-RPC loop over arbitrary `BufRead` / `Write` streams (used by tests and `serve_stdio`).
pub fn serve_stream<R: BufRead, W: Write>(
    reader: &mut R,
    writer: &mut W,
    allow_classes: Option<&str>,
    probe: &mut dyn Probe,
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
        if let Some(resp) = handle_line(trimmed, allow_classes, probe) {
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
            let tools = if is_w1_enabled(allow_classes) {
                vec![watch_tool_definition()]
            } else {
                Vec::new()
            };
            Ok(json!({ "tools": tools }))
        }
        "tools/call" => Ok(call_tool(&params, allow_classes, probe)),
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

fn call_tool(params: &Value, allow_classes: Option<&str>, probe: &mut dyn Probe) -> Value {
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
        "ask" | "lookup" | "task" | "night" => tool_error(&format!(
            "tool {name:?} is disabled in Phase P1: only class W1 (`watch`, no model) is enabled"
        )),
        other => tool_error(&format!("unknown tool: {other}")),
    }
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
    fn mcp_initialize_and_tools_list_expose_only_watch() {
        let mut probe = DummyProbe;
        let init_resp: Value = serde_json::from_str(
            &handle_line(
                r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}"#,
                None,
                &mut probe,
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
            )
            .is_none()
        );

        let list_resp: Value = serde_json::from_str(
            &handle_line(
                r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
                Some("W1"),
                &mut probe,
            )
            .unwrap(),
        )
        .unwrap();
        let tools = list_resp["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["name"], "watch");
    }

    #[test]
    fn mcp_tools_call_executes_watch_and_refuses_other_classes() {
        let mut probe = DummyProbe;
        let call_resp: Value = serde_json::from_str(
            &handle_line(
                r#"{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"watch","arguments":{"kind":"file_line","target":"/tmp/out.log","pattern":"READY"}}}"#,
                Some("W1"),
                &mut probe,
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
        for disabled_tool in ["ask", "lookup", "task", "night"] {
            let req_line = json!({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": { "name": disabled_tool, "arguments": {} }
            })
            .to_string();
            let resp: Value =
                serde_json::from_str(&handle_line(&req_line, Some("W1"), &mut probe).unwrap())
                    .unwrap();
            assert_eq!(resp["result"]["isError"], true);
            assert!(
                resp["result"]["content"][0]["text"]
                    .as_str()
                    .unwrap()
                    .contains("disabled in Phase P1")
            );
        }
    }
}
