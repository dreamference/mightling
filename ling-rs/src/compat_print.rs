//! The compatibility mode's print mode: `ling --compat claude -p "…"` with Claude Code's documented
//! flags and output formats (specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md §6.3, §6.4).
//!
//! In the mode, `-p` is Claude Code's `--print`, not Codex's `--profile`, which stays reachable as
//! `--profile`; outside the mode nothing changes. The launcher runs the task as a child
//! `ling exec --json` and turns Codex's JSON Lines into the format asked for:
//!
//! - `text` (the default): the final answer only.
//! - `json`: one result object at the end, with the fields the public reference lists for a result
//!   message. `total_cost_usd` is always 0, since nothing is billed. Tokens come from the local
//!   server.
//! - `stream-json`: one object per line as things happen: a `system` message of subtype `init`,
//!   assistant messages with text and `tool_use` blocks, user messages with the `tool_result`
//!   blocks, and the final `result`.
//!
//! Only documented message types and fields are written. Codex's items are mapped onto the tool
//! names Claude Code documents (`Bash`, `Edit`/`Write`, `WebSearch`, `Agent`, `mcp__server__tool`).
//! A flag the launcher cannot honour yet is refused with a message, never ignored.
//!
//! The child gets the model server's address in `DREAMFERENCE_VLLM_HOST`, so it does not look for
//! a node a second time, and `--skip-git-repo-check`, because Claude Code's print mode runs in any
//! folder.

use std::collections::HashMap;
use std::ffi::OsString;
use std::io::BufRead;
use std::io::Write;
use std::time::Duration;
use std::time::Instant;

use serde_json::Value;
use serde_json::json;

/// The output formats `--output-format` takes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Format {
    Text,
    Json,
    StreamJson,
}

impl Format {
    fn parse(value: &str) -> Result<Format, String> {
        match value {
            "text" => Ok(Format::Text),
            "json" => Ok(Format::Json),
            "stream-json" => Ok(Format::StreamJson),
            other => Err(format!("--output-format {other} is not a format; use text, json or stream-json")),
        }
    }
}

/// Which session a print run continues.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Resume {
    New,
    /// `-c`, `--continue`: the latest session in this folder.
    Last,
    /// `-r <id>`, `--resume <id>`.
    Id(String),
}

/// Claude Code's `--permission-mode`, as far as print mode maps it (§9.3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PermissionMode {
    /// `default` and `dontAsk`: what would ask is refused (Codex's `exec` never asks).
    Default,
    /// `acceptEdits`: edits inside the workspace go through.
    AcceptEdits,
    /// `bypassPermissions`: Full Access, which the launcher refuses at `/airgapped on`.
    Bypass,
}

/// A print run as the command line asked for it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Request {
    pub prompt: Option<String>,
    pub format: Format,
    pub resume: Resume,
    pub max_turns: Option<u32>,
    pub json_schema: Option<String>,
    pub append_system_prompt: Option<String>,
    pub add_dirs: Vec<String>,
    pub model: Option<String>,
    pub permission_mode: PermissionMode,
    /// `ling`'s own options passed through: `-C`/`--cd`, `--config`, `--profile`.
    pub passthrough: Vec<String>,
    pub debug: bool,
    pub bare: bool,
    /// `--allowedTools` and `--disallowedTools`, as permission rules (compat_permissions.rs).
    pub allowed: Vec<String>,
    pub disallowed: Vec<String>,
}

/// Claude Code's documented print-mode flags this build does not honour yet, each with why.
const NOT_YET: &[(&str, &str)] = &[
    ("--tools", "the tool roster is a later phase (spec §7.2)"),
    ("--input-format", "only text input is read in this build (stream-json input is a later phase)"),
    ("--include-partial-messages", "partial messages are not streamed by the local server's client"),
    ("--system-prompt", "a session's prompt is a named Mightling prompt (ling prompt use); use --append-system-prompt"),
    ("--system-prompt-file", "a session's prompt is a named Mightling prompt (ling prompt use)"),
    ("--permission-prompt-tool", "Codex's exec never asks, so there is no prompt to route"),
    ("--max-budget-usd", "nothing is billed: there is no budget in dollars"),
    ("--mcp-config", "MCP servers from Claude Code's files come in a later phase"),
    ("--strict-mcp-config", "MCP servers from Claude Code's files come in a later phase"),
    ("--settings", "Claude Code's settings files come in a later phase"),
    ("--fork-session", "forking a session in print mode comes in a later phase"),
    ("--session-id", "Codex chooses a session's id"),
    ("--agents", "subagents from Claude Code's files come in a later phase"),
    ("-w", "worktrees in print mode come in a later phase"),
    ("--worktree", "worktrees in print mode come in a later phase"),
];

/// Claude Code's flags that take no value and change nothing here.
const ACCEPTED_SWITCHES: &[&str] = &["--verbose"];

/// Whether this command line is a print run: the mode is on, `-p` or `--print` comes before any
/// `--`, and no Codex subcommand comes before it (`ling exec -p prof` keeps Codex's meaning).
pub fn is_print(user_args: &[String], is_subcommand: impl Fn(&str) -> bool) -> bool {
    let mut index = 0;
    while index < user_args.len() {
        let arg = user_args[index].as_str();
        match arg {
            "--" => return false,
            "-p" | "--print" => return true,
            _ if takes_value(arg) => index += 1,
            _ if !arg.starts_with('-') && is_subcommand(arg) => return false,
            _ => {}
        }
        index += 1;
    }
    false
}

/// The options whose value is the next argument, Claude Code's and the passed-through ones.
fn takes_value(arg: &str) -> bool {
    matches!(
        arg,
        "--output-format"
            | "--max-turns"
            | "--json-schema"
            | "--append-system-prompt"
            | "--append-system-prompt-file"
            | "--add-dir"
            | "--model"
            | "--permission-mode"
            | "--allowedTools"
            | "--allowed-tools"
            | "--disallowedTools"
            | "--disallowed-tools"
            | "-r"
            | "--resume"
            | "-C"
            | "--cd"
            | "--config"
            | "--profile"
    ) || NOT_YET.iter().any(|(flag, _)| *flag == arg && flag_takes_value(flag))
}

fn flag_takes_value(flag: &str) -> bool {
    !matches!(flag, "--include-partial-messages" | "--strict-mcp-config" | "--fork-session")
}

/// Reads a print run's command line (the user's arguments, without the program name).
pub fn parse(user_args: &[String]) -> Result<Request, String> {
    let mut request = Request {
        prompt: None,
        format: Format::Text,
        resume: Resume::New,
        max_turns: None,
        json_schema: None,
        append_system_prompt: None,
        add_dirs: Vec::new(),
        model: None,
        permission_mode: PermissionMode::Default,
        passthrough: Vec::new(),
        debug: false,
        bare: false,
        allowed: Vec::new(),
        disallowed: Vec::new(),
    };
    let mut positional: Vec<String> = Vec::new();
    let mut index = 0;
    while index < user_args.len() {
        let arg = &user_args[index];
        index += 1;
        let (name, inline) = match arg.split_once('=') {
            Some((name, value)) if name.starts_with("--") => (name, Some(value.to_string())),
            _ => (arg.as_str(), None),
        };
        // The option's value: after `=`, or the next argument.
        let mut value = |what: &str| -> Result<String, String> {
            if let Some(value) = inline.clone() {
                return Ok(value);
            }
            let next = user_args.get(index).cloned().ok_or_else(|| format!("{what} needs a value"))?;
            index += 1;
            Ok(next)
        };
        match name {
            "--" => {
                positional.extend(user_args[index..].iter().cloned());
                break;
            }
            "-p" | "--print" => {}
            "--output-format" => request.format = Format::parse(&value(name)?)?,
            "-c" | "--continue" => request.resume = Resume::Last,
            "-r" | "--resume" => {
                // A bare `-r` opens a picker in Claude Code; print mode has none to open.
                let id = match inline.clone() {
                    Some(id) => id,
                    None => match user_args.get(index) {
                        Some(next) if !next.starts_with('-') && !positional_follows_resume(next) => {
                            index += 1;
                            next.clone()
                        }
                        _ => return Err("-r needs a session id in print mode (there is no picker to open)".to_string()),
                    },
                };
                request.resume = Resume::Id(id);
            }
            "--max-turns" => {
                let raw = value(name)?;
                let turns: u32 = raw.parse().map_err(|_| format!("--max-turns {raw} is not a number of turns"))?;
                if turns == 0 {
                    return Err("--max-turns must be at least 1".to_string());
                }
                request.max_turns = Some(turns);
            }
            "--json-schema" => request.json_schema = Some(value(name)?),
            "--append-system-prompt" => request.append_system_prompt = Some(value(name)?),
            "--append-system-prompt-file" => {
                let path = value(name)?;
                let text = std::fs::read_to_string(&path).map_err(|err| format!("could not read {path}: {err}"))?;
                request.append_system_prompt = Some(text);
            }
            "--add-dir" => request.add_dirs.push(value(name)?),
            "--model" => request.model = Some(value(name)?),
            "--permission-mode" => {
                let mode = value(name)?;
                request.permission_mode = match mode.as_str() {
                    "default" | "dontAsk" => PermissionMode::Default,
                    "acceptEdits" => PermissionMode::AcceptEdits,
                    "bypassPermissions" => PermissionMode::Bypass,
                    "plan" => return Err("--permission-mode plan is not built for print mode yet; use /plan in a session".to_string()),
                    "auto" => {
                        return Err("--permission-mode auto is not offered: a second model call per tool call costs the local model too much".to_string());
                    }
                    other => return Err(format!("--permission-mode {other} is not a permission mode")),
                };
            }
            "-C" | "--cd" | "--config" | "--profile" => {
                let given = value(name)?;
                request.passthrough.push(name.to_string());
                request.passthrough.push(given);
            }
            "--allowedTools" | "--allowed-tools" => request.allowed.extend(crate::compat_permissions::split_tool_list(&value(name)?)),
            "--disallowedTools" | "--disallowed-tools" => {
                request.disallowed.extend(crate::compat_permissions::split_tool_list(&value(name)?));
            }
            "--debug" => request.debug = true,
            "--bare" => request.bare = true,
            switch if ACCEPTED_SWITCHES.contains(&switch) => {}
            flag if flag.starts_with('-') && flag.len() > 1 => {
                return Err(match NOT_YET.iter().find(|(known, _)| *known == flag) {
                    Some((_, why)) => format!("{flag} is not supported in the compatibility mode's print mode yet: {why}."),
                    None => format!("{flag} is not an option of print mode (ling --compat claude -p)."),
                });
            }
            _ => positional.push(arg.clone()),
        }
    }
    if positional.len() > 1 {
        return Err(format!("print mode takes one prompt; quote it (got {} words)", positional.len()));
    }
    request.prompt = positional.pop();
    Ok(request)
}

/// Whether the word after `-r` is more likely the prompt than an id. Codex's ids are UUIDs.
fn positional_follows_resume(next: &str) -> bool {
    next.contains(char::is_whitespace)
}

/// The child's command line: `ling exec --json …`, without the program name.
pub fn exec_args(request: &Request, prompt: &str, schema_file: Option<&std::path::Path>) -> Vec<String> {
    let mut args: Vec<String> = vec!["exec".into(), "--json".into(), "--skip-git-repo-check".into()];
    match request.permission_mode {
        PermissionMode::Default => {}
        PermissionMode::AcceptEdits => args.extend(["--sandbox".into(), "workspace-write".into()]),
        PermissionMode::Bypass => args.push("--dangerously-bypass-approvals-and-sandbox".into()),
    }
    for dir in &request.add_dirs {
        args.extend(["--add-dir".into(), dir.clone()]);
    }
    if let Some(model) = &request.model {
        args.extend(["--model".into(), model.clone()]);
    }
    if let Some(file) = schema_file {
        args.extend(["--output-schema".into(), file.display().to_string()]);
    }
    if let Some(text) = &request.append_system_prompt {
        args.extend(["-c".into(), format!("developer_instructions={}", toml_string(text))]);
    }
    args.extend(request.passthrough.iter().cloned());
    match &request.resume {
        Resume::New => {}
        Resume::Last => args.extend(["resume".into(), "--last".into()]),
        Resume::Id(id) => args.extend(["resume".into(), id.clone()]),
    }
    args.extend(["--".into(), prompt.to_string()]);
    args
}

/// A TOML basic string holding `text`, for a `-c` value.
fn toml_string(text: &str) -> String {
    toml::Value::String(text.to_string()).to_string()
}

/// The prompt a run sends: the one given, with piped input after it, or the piped input alone.
pub fn prompt_with_input(prompt: Option<&str>, piped: Option<&str>) -> Option<String> {
    let piped = piped.map(str::trim_end).filter(|text| !text.is_empty());
    match (prompt, piped) {
        (Some(prompt), Some(input)) => Some(format!("{prompt}\n\n{input}")),
        (Some(prompt), None) => Some(prompt.to_string()),
        (None, Some(input)) => Some(input.to_string()),
        (None, None) => None,
    }
}

/// A fresh random id in the UUID form the documented messages carry.
fn new_uuid() -> String {
    let bytes: [u8; 16] = rand::random();
    let hex: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{}-{}-4{}-{:x}{}-{}", &hex[0..8], &hex[8..12], &hex[13..16], (bytes[8] & 0x3) | 0x8, &hex[17..20], &hex[20..32])
}

/// What the translator wants done after an event.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Step {
    Continue,
    /// `--max-turns` was reached: interrupt the child.
    Stop,
}

/// Turns Codex's `exec --json` events into Claude Code's messages.
pub struct Translator {
    format: Format,
    model: String,
    cwd: String,
    context_window: u64,
    max_turns: Option<u32>,
    session_id: String,
    num_turns: u32,
    last_text: Option<String>,
    usage: HashMap<&'static str, i64>,
    web_searches: u64,
    denials: Vec<Value>,
    errors: Vec<String>,
    started_tools: HashMap<String, Instant>,
    tool_time: Duration,
    stopped: bool,
    init_sent: bool,
}

impl Translator {
    pub fn new(format: Format, model: &str, cwd: &str, context_window: u64, max_turns: Option<u32>) -> Translator {
        Translator {
            format,
            model: model.to_string(),
            cwd: cwd.to_string(),
            context_window,
            max_turns,
            session_id: String::new(),
            num_turns: 0,
            last_text: None,
            usage: HashMap::new(),
            web_searches: 0,
            denials: Vec::new(),
            errors: Vec::new(),
            started_tools: HashMap::new(),
            tool_time: Duration::ZERO,
            stopped: false,
            init_sent: false,
        }
    }

    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    /// One event from the child; returns the stream-json lines to write (none for the other
    /// formats) and whether to go on.
    pub fn on_event(&mut self, event: &Value, now: Instant) -> (Vec<Value>, Step) {
        let mut out = Vec::new();
        let kind = event.get("type").and_then(Value::as_str).unwrap_or_default();
        match kind {
            "thread.started" => {
                self.session_id = event.get("thread_id").and_then(Value::as_str).unwrap_or_default().to_string();
                if !self.init_sent {
                    self.init_sent = true;
                    out.push(self.init_message());
                }
            }
            "turn.completed" => {
                if let Some(usage) = event.get("usage") {
                    for (from, to) in [
                        ("input_tokens", "input_tokens"),
                        ("cached_input_tokens", "cached_input_tokens"),
                        ("output_tokens", "output_tokens"),
                        ("reasoning_output_tokens", "reasoning_output_tokens"),
                    ] {
                        *self.usage.entry(to).or_insert(0) += usage.get(from).and_then(Value::as_i64).unwrap_or(0);
                    }
                }
            }
            "turn.failed" => {
                let message = event.pointer("/error/message").and_then(Value::as_str).unwrap_or("the turn failed");
                self.errors.push(message.to_string());
            }
            "error" => {
                let message = event.get("message").and_then(Value::as_str).unwrap_or("error");
                self.errors.push(message.to_string());
            }
            "item.started" => {
                if let Some(item) = event.get("item")
                    && let Some(tool_use) = self.tool_use(item)
                {
                    let id = item_id(item);
                    self.started_tools.insert(id, now);
                    self.num_turns += 1;
                    out.push(self.assistant(vec![tool_use]));
                    if self.max_turns.is_some_and(|max| self.num_turns > max) {
                        self.stopped = true;
                        return (self.stream_only(out), Step::Stop);
                    }
                }
            }
            "item.completed" => {
                if let Some(item) = event.get("item") {
                    out.extend(self.completed(item, now));
                    if self.max_turns.is_some_and(|max| self.num_turns > max) {
                        self.stopped = true;
                        return (self.stream_only(out), Step::Stop);
                    }
                }
            }
            _ => {}
        }
        (self.stream_only(out), Step::Continue)
    }

    fn stream_only(&self, lines: Vec<Value>) -> Vec<Value> {
        if self.format == Format::StreamJson { lines } else { Vec::new() }
    }

    fn init_message(&self) -> Value {
        json!({
            "type": "system",
            "subtype": "init",
            "uuid": new_uuid(),
            "session_id": self.session_id,
            "apiKeySource": "none",
            "cwd": self.cwd,
            "tools": ["Bash", "Edit", "Write", "WebSearch", "Agent"],
            "mcp_servers": [],
            "model": self.model,
            "permissionMode": "default",
            "slash_commands": [],
            "output_style": "default",
            "skills": [],
            "plugins": [],
        })
    }

    fn assistant(&self, content: Vec<Value>) -> Value {
        json!({
            "type": "assistant",
            "uuid": new_uuid(),
            "session_id": self.session_id,
            "parent_tool_use_id": null,
            "message": {
                "id": format!("msg_{}", new_uuid().replace('-', "")),
                "type": "message",
                "role": "assistant",
                "model": self.model,
                "content": content,
                "stop_reason": null,
                "stop_sequence": null,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })
    }

    fn user_result(&self, tool_use_id: &str, content: &str, is_error: bool) -> Value {
        json!({
            "type": "user",
            "uuid": new_uuid(),
            "session_id": self.session_id,
            "parent_tool_use_id": null,
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}],
            },
        })
    }

    /// The `tool_use` block for a Codex item that is a tool call, named as Claude Code names it.
    fn tool_use(&self, item: &Value) -> Option<Value> {
        let id = item_id(item);
        let (name, input) = match item.get("type").and_then(Value::as_str)? {
            "command_execution" => {
                let command = item.get("command").and_then(Value::as_str).unwrap_or_default();
                ("Bash".to_string(), json!({"command": without_shell_wrapper(command)}))
            }
            "file_change" => {
                let changes = item.get("changes").and_then(Value::as_array).cloned().unwrap_or_default();
                let only_adds = !changes.is_empty() && changes.iter().all(|c| c.get("kind").and_then(Value::as_str) == Some("add"));
                let paths: Vec<Value> = changes.iter().filter_map(|c| c.get("path").cloned()).collect();
                let name = if only_adds { "Write" } else { "Edit" };
                let input = match paths.as_slice() {
                    [one] => json!({"file_path": one}),
                    _ => json!({"file_paths": paths}),
                };
                (name.to_string(), input)
            }
            "mcp_tool_call" => {
                let server = item.get("server").and_then(Value::as_str).unwrap_or_default();
                let tool = item.get("tool").and_then(Value::as_str).unwrap_or_default();
                (format!("mcp__{server}__{tool}"), item.get("arguments").cloned().unwrap_or(json!({})))
            }
            "web_search" => ("WebSearch".to_string(), json!({"query": item.get("query").cloned().unwrap_or(json!(""))})),
            "collab_tool_call" => ("Agent".to_string(), json!({"prompt": item.get("prompt").cloned().unwrap_or(Value::Null)})),
            _ => return None,
        };
        Some(json!({"type": "tool_use", "id": id, "name": name, "input": input}))
    }

    fn completed(&mut self, item: &Value, now: Instant) -> Vec<Value> {
        let id = item_id(item);
        let kind = item.get("type").and_then(Value::as_str).unwrap_or_default();
        if kind == "agent_message" {
            let text = item.get("text").and_then(Value::as_str).unwrap_or_default().to_string();
            self.num_turns += 1;
            self.last_text = Some(text.clone());
            return vec![self.assistant(vec![json!({"type": "text", "text": text})])];
        }
        // An `error` item is a notice Codex records in the thread (a feature it fell back from,
        // say); the turn goes on, so it does not fail the run. A failed turn does.
        if kind == "error" {
            return Vec::new();
        }
        let Some(tool_use) = self.tool_use(item) else { return Vec::new() };
        let mut out = Vec::new();
        match self.started_tools.remove(&id) {
            Some(started) => self.tool_time += now.saturating_duration_since(started),
            None => {
                // Seen only when it finished: its call is shown first.
                self.num_turns += 1;
                out.push(self.assistant(vec![tool_use.clone()]));
            }
        }
        if kind == "web_search" {
            self.web_searches += 1;
        }
        let status = item.get("status").and_then(Value::as_str).unwrap_or("completed");
        if status == "declined" {
            self.denials.push(json!({
                "tool_name": tool_use["name"],
                "tool_use_id": id,
                "tool_input": tool_use["input"],
            }));
        }
        let (content, is_error) = match kind {
            "command_execution" => {
                let output = item.get("aggregated_output").and_then(Value::as_str).unwrap_or_default().to_string();
                let failed = status != "completed" || item.get("exit_code").and_then(Value::as_i64).is_some_and(|code| code != 0);
                (output, failed)
            }
            "mcp_tool_call" => match item.get("error").and_then(|e| e.get("message")).and_then(Value::as_str) {
                Some(message) => (message.to_string(), true),
                None => (item.get("result").map(|r| r.to_string()).unwrap_or_default(), false),
            },
            _ => (status.to_string(), status == "failed"),
        };
        out.push(self.user_result(&id, &content, is_error));
        out
    }

    /// The final result message.
    pub fn result(&self, elapsed: Duration, structured: Option<Value>) -> Value {
        let input = self.usage.get("input_tokens").copied().unwrap_or(0);
        let cached = self.usage.get("cached_input_tokens").copied().unwrap_or(0);
        let output = self.usage.get("output_tokens").copied().unwrap_or(0);
        let thinking = self.usage.get("reasoning_output_tokens").copied().unwrap_or(0);
        let usage = json!({
            "input_tokens": (input - cached).max(0),
            "output_tokens": output,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": cached,
        });
        let mut model_usage = serde_json::Map::new();
        if !self.model.is_empty() {
            model_usage.insert(
                self.model.clone(),
                json!({
                    "inputTokens": (input - cached).max(0),
                    "outputTokens": output,
                    "thinkingTokens": thinking,
                    "cacheReadInputTokens": cached,
                    "cacheCreationInputTokens": 0,
                    "webSearchRequests": self.web_searches,
                    "costUSD": 0,
                    "contextWindow": self.context_window,
                    "maxOutputTokens": self.context_window,
                }),
            );
        }
        let duration_ms = elapsed.as_millis() as u64;
        let api_ms = elapsed.saturating_sub(self.tool_time).as_millis() as u64;
        let failed = self.stopped || !self.errors.is_empty() || self.last_text.is_none();
        let mut result = json!({
            "type": "result",
            "subtype": "success",
            "uuid": new_uuid(),
            "session_id": self.session_id,
            "duration_ms": duration_ms,
            "duration_api_ms": api_ms,
            "is_error": false,
            "num_turns": self.num_turns,
            "stop_reason": "end_turn",
            "total_cost_usd": 0,
            "usage": usage,
            "modelUsage": model_usage,
            "permission_denials": self.denials,
        });
        if failed {
            result["subtype"] = json!(if self.stopped { "error_max_turns" } else { "error_during_execution" });
            result["is_error"] = json!(true);
            result["stop_reason"] = Value::Null;
            let mut errors = self.errors.clone();
            if self.stopped {
                errors.push(format!("stopped after {} turns (--max-turns)", self.max_turns.unwrap_or_default()));
            } else if errors.is_empty() {
                errors.push("the session ended without an answer".to_string());
            }
            result["errors"] = json!(errors);
        } else {
            result["result"] = json!(self.last_text.clone().unwrap_or_default());
            if let Some(structured) = structured {
                result["structured_output"] = structured;
            }
        }
        result
    }

    /// The final answer's text, for `text` output and `--json-schema`.
    pub fn last_text(&self) -> Option<&str> {
        self.last_text.as_deref()
    }
}

/// The command a person typed, from Codex's record of how it ran it: `/bin/bash -lc 'cmd'`
/// becomes `cmd`. Anything else is returned as it is.
pub fn without_shell_wrapper(command: &str) -> String {
    for shell in ["/bin/bash", "/usr/bin/bash", "bash", "/bin/zsh", "/usr/bin/zsh", "zsh", "/bin/sh", "sh"] {
        for flag in ["-lc", "-c"] {
            let Some(rest) = command.strip_prefix(shell).and_then(|r| r.strip_prefix(' ')).and_then(|r| r.strip_prefix(flag)).and_then(|r| r.strip_prefix(' '))
            else {
                continue;
            };
            // One single-quoted word, with `'\''` standing for a quote inside it.
            if let Some(inner) = rest.strip_prefix('\'').and_then(|r| r.strip_suffix('\''))
                && !inner.replace("'\\''", "").contains('\'')
            {
                return inner.replace("'\\''", "'");
            }
            if !rest.contains(['\'', '"', ' ']) {
                return rest.to_string();
            }
        }
    }
    command.to_string()
}

fn item_id(item: &Value) -> String {
    item.get("id").and_then(Value::as_str).unwrap_or_default().to_string()
}

/// Runs a print run and returns the process's exit code. `user_args` are the arguments without
/// the program name and without `--compat`.
pub async fn run(user_args: &[String]) -> i32 {
    match run_inner(user_args).await {
        Ok(code) => code,
        Err(err) => {
            eprintln!("{err}");
            1
        }
    }
}

async fn run_inner(user_args: &[String]) -> anyhow::Result<i32> {
    use std::io::IsTerminal;
    use std::io::Read;
    let request = match parse(user_args) {
        Ok(request) => request,
        Err(message) => {
            eprintln!("{message}");
            return Ok(2);
        }
    };
    let piped = if std::io::stdin().is_terminal() {
        None
    } else {
        let mut text = String::new();
        std::io::stdin().read_to_string(&mut text).ok();
        Some(text)
    };
    let Some(prompt) = prompt_with_input(request.prompt.as_deref(), piped.as_deref()) else {
        eprintln!("print mode needs a prompt: ling --compat claude -p \"…\", or pipe it in.");
        return Ok(2);
    };
    // A custom command or a skill typed as `/name args`, as at Claude Code's prompt (compat_commands.rs).
    let prompt = std::env::current_dir()
        .ok()
        .and_then(|cwd| crate::compat_commands::expand_prompt(&prompt, &cwd))
        .unwrap_or(prompt);
    // The model server, found once here and handed to the child, and the model it serves.
    let host = crate::node::resolve_host(false).await?;
    let client = reqwest::Client::builder().timeout(Duration::from_secs(10)).no_proxy().build()?;
    let served = crate::served_model(&client, &host).await;
    if let (Some(asked), Some(served)) = (&request.model, &served)
        && *asked != served.id
    {
        eprintln!("--model {asked} is not served here; the model server serves {}.", served.id);
        return Ok(2);
    }
    let (model, window) = served.map(|m| (m.id, m.max_model_len)).unwrap_or_default();
    let schema_file = match &request.json_schema {
        Some(schema) => {
            serde_json::from_str::<Value>(schema).map_err(|err| anyhow::anyhow!("--json-schema is not JSON: {err}"))?;
            let path = std::env::temp_dir().join(format!("ling-compat-schema-{}.json", std::process::id()));
            std::fs::write(&path, schema)?;
            Some(path)
        }
        None => None,
    };
    let args = exec_args(&request, &prompt, schema_file.as_deref());
    let mut command = std::process::Command::new(std::env::current_exe()?);
    command
        .args(args.iter().map(OsString::from))
        .env(crate::compat::ENV, "claude")
        .env("DREAMFERENCE_VLLM_HOST", &host)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped());
    if request.debug {
        command.env("RUST_LOG", "debug");
    }
    if request.bare {
        command.env(BARE_ENV, "1");
    }
    if !request.allowed.is_empty() || !request.disallowed.is_empty() {
        command.env(crate::compat_permissions::RULES_ENV, crate::compat_permissions::rules_to_env(&request.allowed, &request.disallowed));
    }
    let started = Instant::now();
    let mut child = command.spawn()?;
    let cwd = std::env::current_dir().map(|dir| dir.display().to_string()).unwrap_or_default();
    let mut translator = Translator::new(request.format, &model, &cwd, window, request.max_turns);
    let stdout = child.stdout.take().ok_or_else(|| anyhow::anyhow!("the session's output could not be read"))?;
    let mut out = std::io::stdout().lock();
    for line in std::io::BufReader::new(stdout).lines() {
        let Ok(line) = line else { break };
        let Ok(event) = serde_json::from_str::<Value>(&line) else { continue };
        let (lines, step) = translator.on_event(&event, Instant::now());
        for message in lines {
            writeln!(out, "{message}")?;
        }
        out.flush()?;
        if step == Step::Stop {
            interrupt(&mut child);
            break;
        }
    }
    let status = child.wait()?;
    if let Some(path) = &schema_file {
        let _ = std::fs::remove_file(path);
    }
    let structured = schema_file
        .as_ref()
        .and_then(|_| translator.last_text())
        .and_then(|text| serde_json::from_str::<Value>(text.trim()).ok());
    let mut result = translator.result(started.elapsed(), structured);
    if !status.success() && result["is_error"] == json!(false) {
        result["subtype"] = json!("error_during_execution");
        result["is_error"] = json!(true);
        result["errors"] = json!([format!("the session exited with {status}")]);
    }
    let failed = result["is_error"] == json!(true);
    match request.format {
        Format::Text => {
            if failed {
                let errors: Vec<String> = result["errors"]
                    .as_array()
                    .map(|list| list.iter().filter_map(Value::as_str).map(str::to_string).collect())
                    .unwrap_or_default();
                writeln!(out, "{}", errors.join("\n"))?;
            } else {
                writeln!(out, "{}", result["result"].as_str().unwrap_or_default())?;
            }
        }
        Format::Json | Format::StreamJson => writeln!(out, "{result}")?,
    }
    out.flush()?;
    Ok(if failed { 1 } else { 0 })
}

/// Set for a print run given `--bare`: the compatibility reads of later phases skip Claude Code's
/// files when it is set.
pub const BARE_ENV: &str = "MIGHTLING_COMPAT_BARE";

/// Interrupts the child the way Ctrl+C would, so Codex ends its turn and exits, then stops it if
/// it has not within a few seconds.
fn interrupt(child: &mut std::process::Child) {
    #[cfg(unix)]
    {
        // SAFETY: a signal to our own child's pid.
        unsafe { libc::kill(child.id() as libc::pid_t, libc::SIGINT) };
        for _ in 0..50 {
            if matches!(child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    let _ = child.kill();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(args: &[&str]) -> Vec<String> {
        args.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn print_mode_is_dash_p_before_any_subcommand() {
        let sub = |word: &str| matches!(word, "exec" | "resume" | "mcp");
        assert!(is_print(&strings(&["-p", "hi"]), sub));
        assert!(is_print(&strings(&["--output-format", "json", "--print", "hi"]), sub));
        assert!(is_print(&strings(&["--model", "exec", "-p", "hi"]), sub), "a flag's value is not a subcommand");
        assert!(!is_print(&strings(&["exec", "-p", "prof", "hi"]), sub), "Codex's -p after its subcommand");
        assert!(!is_print(&strings(&["--", "-p"]), sub));
        assert!(!is_print(&strings(&["hello"]), sub));
    }

    #[test]
    fn the_documented_flags_are_read_and_the_rest_refused() {
        let r = parse(&strings(&[
            "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "3", "--append-system-prompt", "Be terse.",
            "--add-dir", "../lib", "--permission-mode", "acceptEdits", "-C", "sub", "explain this",
        ]))
        .unwrap();
        assert_eq!(r.format, Format::StreamJson);
        assert_eq!(r.max_turns, Some(3));
        assert_eq!(r.append_system_prompt.as_deref(), Some("Be terse."));
        assert_eq!(r.add_dirs, ["../lib"]);
        assert_eq!(r.permission_mode, PermissionMode::AcceptEdits);
        assert_eq!(r.passthrough, ["-C", "sub"]);
        assert_eq!(r.prompt.as_deref(), Some("explain this"));
        let r = parse(&strings(&["-p", "--allowedTools", "Bash(npm *),Read", "--disallowedTools=Bash(git push *)", "x"])).unwrap();
        assert_eq!(r.allowed, ["Bash(npm *)", "Read"]);
        assert_eq!(r.disallowed, ["Bash(git push *)"]);
        let r = parse(&strings(&["-p", "-c", "go on"])).unwrap();
        assert_eq!(r.resume, Resume::Last);
        let r = parse(&strings(&["-p", "--resume=0199a213-81c0-7800-8aa1-bbab2a035a53", "go on"])).unwrap();
        assert_eq!(r.resume, Resume::Id("0199a213-81c0-7800-8aa1-bbab2a035a53".into()));
        let r = parse(&strings(&["-p", "--output-format=json", "x"])).unwrap();
        assert_eq!(r.format, Format::Json);
        for refused in [
            vec!["-p", "--max-budget-usd", "1", "x"],
            vec!["-p", "--output-format", "xml", "x"],
            vec!["-p", "--permission-mode", "auto", "x"],
            vec!["-p", "--max-turns", "0", "x"],
            vec!["-p", "--frobnicate", "x"],
            vec!["-p", "two", "prompts"],
            vec!["-p", "-r", "--verbose"],
        ] {
            assert!(parse(&strings(&refused)).is_err(), "{refused:?}");
        }
        assert!(parse(&strings(&["-p", "--max-budget-usd", "1", "x"])).unwrap_err().contains("nothing is billed"));
    }

    #[test]
    fn the_child_is_codex_exec_with_json_events() {
        let r = parse(&strings(&["-p", "--permission-mode", "bypassPermissions", "--model", "m", "--append-system-prompt", "say \"hi\""])).unwrap();
        let args = exec_args(&r, "do it", Some(std::path::Path::new("/tmp/s.json")));
        assert_eq!(&args[..3], ["exec", "--json", "--skip-git-repo-check"]);
        assert!(args.contains(&"--dangerously-bypass-approvals-and-sandbox".to_string()));
        assert!(args.windows(2).any(|w| w == ["--output-schema", "/tmp/s.json"]));
        let setting = args.iter().position(|a| a == "-c").map(|i| args[i + 1].clone()).unwrap();
        let parsed: toml::Table = toml::from_str(&setting).unwrap();
        assert_eq!(parsed["developer_instructions"].as_str(), Some("say \"hi\""), "{setting}");
        assert_eq!(&args[args.len() - 2..], ["--", "do it"]);
        let r = parse(&strings(&["-p", "-c", "more"])).unwrap();
        let args = exec_args(&r, "more", None);
        assert!(args.windows(2).any(|w| w == ["resume", "--last"]));
        let r = parse(&strings(&["-p", "-r", "abc", "more"])).unwrap();
        assert!(exec_args(&r, "more", None).windows(2).any(|w| w == ["resume", "abc"]));
    }

    #[test]
    fn piped_input_follows_the_prompt() {
        assert_eq!(prompt_with_input(Some("explain"), Some("error: x\n")).as_deref(), Some("explain\n\nerror: x"));
        assert_eq!(prompt_with_input(None, Some("just this\n")).as_deref(), Some("just this"));
        assert_eq!(prompt_with_input(Some("p"), Some("  \n")).as_deref(), Some("p"));
        assert_eq!(prompt_with_input(None, None), None);
    }

    /// A recorded-shape `exec --json` stream: a command, a file edit, the answer.
    fn events() -> Vec<Value> {
        [
            r#"{"type":"thread.started","thread_id":"t-1"}"#,
            r#"{"type":"turn.started"}"#,
            r#"{"type":"item.started","item":{"id":"item_0","type":"command_execution","command":"ls","aggregated_output":"","exit_code":null,"status":"in_progress"}}"#,
            r#"{"type":"item.completed","item":{"id":"item_0","type":"command_execution","command":"ls","aggregated_output":"a.txt\n","exit_code":0,"status":"completed"}}"#,
            r#"{"type":"item.completed","item":{"id":"item_1","type":"file_change","changes":[{"path":"b.txt","kind":"add"}],"status":"completed"}}"#,
            r#"{"type":"item.completed","item":{"id":"item_2","type":"command_execution","command":"rm -rf /","aggregated_output":"","exit_code":null,"status":"declined"}}"#,
            r#"{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"{\"done\": true}"}}"#,
            r#"{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":400,"output_tokens":50,"reasoning_output_tokens":10}}"#,
        ]
        .iter()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
    }

    #[test]
    fn stream_json_has_init_tool_calls_results_and_the_answer() {
        let mut t = Translator::new(Format::StreamJson, "qwen", "/w", 65536, None);
        let now = Instant::now();
        let lines: Vec<Value> = events().iter().flat_map(|e| t.on_event(e, now).0).collect();
        let kinds: Vec<&str> = lines.iter().map(|l| l["type"].as_str().unwrap()).collect();
        assert_eq!(kinds, ["system", "assistant", "user", "assistant", "user", "assistant", "user", "assistant"]);
        assert_eq!(lines[0]["subtype"], "init");
        assert_eq!(lines[0]["session_id"], "t-1");
        assert_eq!(lines[1]["message"]["content"][0]["name"], "Bash");
        assert_eq!(lines[1]["message"]["content"][0]["input"]["command"], "ls");
        assert_eq!(lines[2]["message"]["content"][0]["tool_use_id"], "item_0");
        assert_eq!(lines[2]["message"]["content"][0]["content"], "a.txt\n");
        assert_eq!(lines[3]["message"]["content"][0]["name"], "Write");
        assert_eq!(lines[3]["message"]["content"][0]["input"]["file_path"], "b.txt");
        assert_eq!(lines[7]["message"]["content"][0]["type"], "text");
        let result = t.result(Duration::from_millis(1500), None);
        assert_eq!(result["type"], "result");
        assert_eq!(result["subtype"], "success");
        assert_eq!(result["is_error"], false);
        assert_eq!(result["result"], "{\"done\": true}");
        assert_eq!(result["session_id"], "t-1");
        assert_eq!(result["num_turns"], 4);
        assert_eq!(result["total_cost_usd"], 0);
        assert_eq!(result["usage"]["input_tokens"], 600);
        assert_eq!(result["usage"]["cache_read_input_tokens"], 400);
        assert_eq!(result["modelUsage"]["qwen"]["contextWindow"], 65536);
        assert_eq!(result["permission_denials"][0]["tool_name"], "Bash");
        assert_eq!(result["permission_denials"][0]["tool_input"]["command"], "rm -rf /");
        assert!(result.get("errors").is_none());
    }

    #[test]
    fn json_and_text_write_nothing_until_the_end_and_a_schema_answer_is_structured() {
        let mut t = Translator::new(Format::Json, "qwen", "/w", 1, None);
        let now = Instant::now();
        assert!(events().iter().all(|e| t.on_event(e, now).0.is_empty()));
        let structured = t.last_text().and_then(|text| serde_json::from_str::<Value>(text).ok());
        let result = t.result(Duration::from_millis(10), structured);
        assert_eq!(result["structured_output"]["done"], true);
    }

    #[test]
    fn max_turns_stops_the_run_and_failures_are_results_too() {
        let mut t = Translator::new(Format::Json, "qwen", "/w", 1, Some(1));
        let now = Instant::now();
        let mut steps = Vec::new();
        for event in events() {
            let (_, step) = t.on_event(&event, now);
            steps.push(step);
            if step == Step::Stop {
                break;
            }
        }
        assert_eq!(steps.last(), Some(&Step::Stop));
        let result = t.result(Duration::from_millis(10), None);
        assert_eq!(result["subtype"], "error_max_turns");
        assert_eq!(result["is_error"], true);
        assert!(result.get("result").is_none());
        let mut t = Translator::new(Format::Text, "", "/w", 1, None);
        t.on_event(&serde_json::from_str(r#"{"type":"thread.started","thread_id":"t"}"#).unwrap(), now);
        t.on_event(&serde_json::from_str(r#"{"type":"turn.failed","error":{"message":"stream disconnected"}}"#).unwrap(), now);
        let result = t.result(Duration::from_millis(10), None);
        assert_eq!(result["subtype"], "error_during_execution");
        assert_eq!(result["errors"][0], "stream disconnected");
        assert!(result["modelUsage"].as_object().unwrap().is_empty());
    }

    #[test]
    fn every_subcommand_the_launcher_intercepts_is_known_to_print_mode() {
        let source = include_str!("lib.rs");
        let marker = "user_args[index] == \"";
        let found: std::collections::BTreeSet<&str> = source
            .match_indices(marker)
            .filter_map(|(at, _)| source[at + marker.len()..].split('"').next())
            .filter(|word| *word != "app-server")
            .collect();
        for word in &found {
            assert!(crate::LAUNCHER_SUBCOMMANDS.contains(word), "{word} is intercepted but not in LAUNCHER_SUBCOMMANDS");
        }
        assert!(found.len() >= 10, "{found:?}");
    }

    #[test]
    fn the_shell_wrapper_is_taken_off_commands() {
        assert_eq!(without_shell_wrapper("/bin/bash -lc 'echo hi'"), "echo hi");
        assert_eq!(without_shell_wrapper("bash -c ls"), "ls");
        assert_eq!(without_shell_wrapper(r"/bin/bash -lc 'echo '\''a b'\'''"), "echo 'a b'");
        assert_eq!(without_shell_wrapper("rg -n foo"), "rg -n foo");
        assert_eq!(without_shell_wrapper("/bin/bash -lc 'a' 'b'"), "/bin/bash -lc 'a' 'b'");
    }

    #[test]
    fn an_error_item_is_a_notice_and_does_not_fail_the_run() {
        let mut t = Translator::new(Format::Json, "m", "/w", 1, None);
        let now = Instant::now();
        for line in [
            r#"{"type":"thread.started","thread_id":"t"}"#,
            r#"{"type":"item.completed","item":{"id":"i0","type":"error","message":"Code Mode is unavailable"}}"#,
            r#"{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"ok"}}"#,
        ] {
            t.on_event(&serde_json::from_str(line).unwrap(), now);
        }
        let result = t.result(Duration::from_millis(1), None);
        assert_eq!(result["subtype"], "success");
        assert_eq!(result["result"], "ok");
    }

    #[test]
    fn ids_look_like_uuids() {
        let id = new_uuid();
        assert_eq!(id.len(), 36);
        assert_eq!(id.chars().filter(|c| *c == '-').count(), 4);
        assert_eq!(id.as_bytes()[14], b'4');
        assert_ne!(new_uuid(), id);
    }
}
