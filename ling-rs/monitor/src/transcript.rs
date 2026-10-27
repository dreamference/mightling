//! One line of a Claude Code transcript, reduced to the few fields the checks use (§7.1).
//!
//! The format is undocumented and changes between versions, so the reader keys on a handful of
//! fields (`type`, `timestamp`, `message.id`, `message.usage`, the `tool_use`, `tool_result` and
//! `text` blocks, and an API error's `error` and `isApiErrorMessage`) and skips every line type it
//! does not use. A tool result's text is reduced to flags here, at parse time, so no transcript text
//! is kept anywhere.

use chrono::DateTime;
use serde_json::Value;

/// Token counts of one model response, from `message.usage`.
#[derive(Debug, Clone, Copy, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Usage {
    pub input: u64,
    pub cache_write: u64,
    pub cache_read: u64,
    pub output: u64,
    /// The context of the response's last model call, when the response made several (a server
    /// tool such as an advisor runs between them, and the top-level counts are their sum).
    #[serde(default)]
    pub last_call_context: Option<u64>,
}

impl Usage {
    /// The context the response was computed over: input, cache writes and cache reads (of its
    /// last model call, when it made several).
    pub fn context(&self) -> u64 {
        self.last_call_context.unwrap_or(self.input + self.cache_write + self.cache_read)
    }

    /// Tokens times the list-price ratios of §2: input 1, cache write 1.25, cache read 0.1, output 5.
    pub fn weighted(&self) -> f64 {
        self.input as f64 + 1.25 * self.cache_write as f64 + 0.1 * self.cache_read as f64 + 5.0 * self.output as f64
    }
}

/// One `tool_use` block: what the checks need of it, never its whole input.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolUse {
    pub id: String,
    pub name: String,
    /// `input.command` of a shell call.
    pub command: Option<String>,
    /// `input.file_path` (or `path`, `notebook_path`) of a file tool.
    pub file_path: Option<String>,
    /// `input.run_in_background`: the call returns at once and its result is not a wait.
    pub background: bool,
}

/// What a tool result said, as flags: the text itself is dropped.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct ResultFlags {
    /// The repository's pre-push hook refused a push (its fixed refusal line).
    pub push_refused: bool,
    /// `tests/conftest.py` stopped a test that reached for the real machine ("a test tried to ...").
    pub home_guard: bool,
    /// A GitHub Actions run finished: `Some(true)` for success, `Some(false)` for failure.
    pub run_succeeded: Option<bool>,
    /// The text names a release workflow.
    pub mentions_release: bool,
}

impl ResultFlags {
    pub fn of(text: &str) -> ResultFlags {
        let lower = text.to_ascii_lowercase();
        let run_succeeded = if lower.contains("completed with 'failure'") || lower.contains("\"conclusion\":\"failure\"") {
            Some(false)
        } else if lower.contains("completed with 'success'") || lower.contains("\"conclusion\":\"success\"") {
            Some(true)
        } else {
            None
        };
        ResultFlags {
            push_refused: text.contains("pre-push:") && text.contains("push refused"),
            home_guard: text.contains("a test tried to "),
            run_succeeded,
            mentions_release: lower.contains("release"),
        }
    }
}

/// What one line contributes.
#[derive(Debug, Clone, PartialEq)]
pub enum Entry {
    /// A model response (one content block per line; several lines share one `id`).
    Response { id: String, usage: Option<Usage>, tools: Vec<ToolUse>, text: Option<String> },
    /// An API error written as an assistant line (`isApiErrorMessage`), such as a usage limit.
    ApiError { error: String, text: String },
    /// The result of a tool call.
    ToolResult { tool_use_id: String, is_error: bool, flags: ResultFlags },
    /// A user turn: the owner's message, a notification, or an interruption.
    UserText { interrupted: bool },
}

/// How the reader classed a line, for `doctor` and the format-drift check.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LineClass {
    /// A `user` or `assistant` line read as expected.
    Used,
    /// A line type the monitor reads past on purpose (attachments, request records, titles, ...).
    Skipped,
    /// A line type this reader has never seen: skipped, and named by `doctor`.
    UnknownType,
    /// Not JSON, or a `user`/`assistant` line without the fields the checks need.
    Unclassed,
}

/// One parsed line.
#[derive(Debug, Clone, PartialEq)]
pub struct Line {
    pub ts: Option<i64>,
    pub kind: Option<String>,
    pub cwd: Option<String>,
    pub version: Option<String>,
    pub entries: Vec<Entry>,
    pub class: LineClass,
}

/// Line types Claude Code writes that carry nothing the checks use.
const SKIPPED_TYPES: &[&str] = &[
    "attachment", "system", "summary", "file-history-snapshot", "file-history-delta", "queue-operation", "progress",
    "ai-title", "custom-title", "api-request", "api-request-blob", "api-request-shape", "atis-latch", "bridge-session",
    "history-suppression", "last-prompt", "mode", "permission-mode", "fork-context-ref", "tag", "agent-name", "cost-state",
];

/// RFC 3339 to epoch seconds.
pub fn parse_ts(text: &str) -> Option<i64> {
    DateTime::parse_from_rfc3339(text).ok().map(|t| t.timestamp())
}

fn usage_of(value: &Value) -> Option<Usage> {
    let usage = value.get("usage")?;
    let field = |name: &str| usage.get(name).and_then(Value::as_u64).unwrap_or(0);
    let last_call_context = usage.get("iterations").and_then(Value::as_array).and_then(|calls| {
        calls.iter().rev().find(|c| c.get("type").and_then(Value::as_str) == Some("message")).map(|c| {
            let n = |name: &str| c.get(name).and_then(Value::as_u64).unwrap_or(0);
            n("input_tokens") + n("cache_creation_input_tokens") + n("cache_read_input_tokens")
        })
    });
    Some(Usage {
        input: field("input_tokens"),
        cache_write: field("cache_creation_input_tokens"),
        cache_read: field("cache_read_input_tokens"),
        output: field("output_tokens"),
        last_call_context: last_call_context.filter(|_| usage.get("iterations").and_then(Value::as_array).is_some_and(|a| a.len() > 1)),
    })
}

fn text_of(content: &Value) -> String {
    match content {
        Value::String(text) => text.clone(),
        Value::Array(blocks) => blocks
            .iter()
            .filter_map(|block| block.get("text").and_then(Value::as_str))
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

fn tool_use_of(block: &Value) -> ToolUse {
    let input = block.get("input");
    let string = |name: &str| input.and_then(|i| i.get(name)).and_then(Value::as_str).map(str::to_string);
    ToolUse {
        id: block.get("id").and_then(Value::as_str).unwrap_or_default().to_string(),
        name: block.get("name").and_then(Value::as_str).unwrap_or_default().to_string(),
        command: string("command"),
        file_path: string("file_path").or_else(|| string("notebook_path")).or_else(|| string("path")),
        background: input.and_then(|i| i.get("run_in_background")).and_then(Value::as_bool).unwrap_or(false),
    }
}

/// Parses one line of a transcript. Never fails: a line it cannot use is classed, not an error.
pub fn parse_line(raw: &str) -> Line {
    let mut line = Line { ts: None, kind: None, cwd: None, version: None, entries: Vec::new(), class: LineClass::Unclassed };
    let Ok(value) = serde_json::from_str::<Value>(raw) else { return line };
    let string = |name: &str| value.get(name).and_then(Value::as_str).map(str::to_string);
    line.ts = string("timestamp").as_deref().and_then(parse_ts);
    line.kind = string("type");
    line.cwd = string("cwd");
    line.version = string("version");
    let Some(kind) = line.kind.clone() else { return line };
    let message = value.get("message");
    match kind.as_str() {
        "assistant" => {
            let Some(message) = message else { return line };
            if value.get("isApiErrorMessage").and_then(Value::as_bool) == Some(true) {
                let error = string("error").unwrap_or_else(|| "unknown".to_string());
                let text = message.get("content").map(text_of).unwrap_or_default();
                line.entries.push(Entry::ApiError { error, text });
                line.class = LineClass::Used;
                return line;
            }
            let Some(id) = message.get("id").and_then(Value::as_str) else { return line };
            if message.get("model").and_then(Value::as_str) == Some("<synthetic>") {
                line.class = LineClass::Skipped;
                return line;
            }
            let mut tools = Vec::new();
            let mut texts = Vec::new();
            if let Some(Value::Array(blocks)) = message.get("content") {
                for block in blocks {
                    match block.get("type").and_then(Value::as_str) {
                        Some("tool_use") => tools.push(tool_use_of(block)),
                        Some("text") => texts.extend(block.get("text").and_then(Value::as_str).map(str::to_string)),
                        _ => {}
                    }
                }
            }
            let text = (!texts.is_empty()).then(|| texts.join("\n"));
            line.entries.push(Entry::Response { id: id.to_string(), usage: usage_of(message), tools, text });
            line.class = LineClass::Used;
        }
        "user" => {
            let Some(content) = message.and_then(|m| m.get("content")) else { return line };
            match content {
                Value::String(text) => {
                    line.entries.push(Entry::UserText { interrupted: text.contains("[Request interrupted by user") })
                }
                Value::Array(blocks) => {
                    let mut said = false;
                    let mut interrupted = false;
                    for block in blocks {
                        match block.get("type").and_then(Value::as_str) {
                            Some("tool_result") => {
                                let text = block.get("content").map(text_of).unwrap_or_default();
                                line.entries.push(Entry::ToolResult {
                                    tool_use_id: block.get("tool_use_id").and_then(Value::as_str).unwrap_or_default().to_string(),
                                    is_error: block.get("is_error").and_then(Value::as_bool).unwrap_or(false),
                                    flags: ResultFlags::of(&text),
                                });
                            }
                            Some("text") => {
                                said = true;
                                let text = block.get("text").and_then(Value::as_str).unwrap_or_default();
                                interrupted |= text.contains("[Request interrupted by user");
                            }
                            _ => said = true,
                        }
                    }
                    if said && line.entries.is_empty() {
                        line.entries.push(Entry::UserText { interrupted });
                    }
                }
                _ => return line,
            }
            line.class = LineClass::Used;
        }
        other if SKIPPED_TYPES.contains(&other) => line.class = LineClass::Skipped,
        _ => line.class = LineClass::UnknownType,
    }
    line
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_response_line_gives_its_id_usage_and_tool_call() {
        let raw = r#"{"type":"assistant","timestamp":"2026-10-26T10:00:00.000Z","cwd":"/home/u/repo","version":"2.9.1","message":{"id":"msg_1","model":"m","content":[{"type":"tool_use","id":"toolu_1","name":"Bash","input":{"command":"sleep 60","run_in_background":true}}],"usage":{"input_tokens":2,"cache_creation_input_tokens":1000,"cache_read_input_tokens":50000,"output_tokens":40}}}"#;
        let line = parse_line(raw);
        assert_eq!(line.class, LineClass::Used);
        assert_eq!(line.ts, Some(1_793_008_800));
        assert_eq!(line.version.as_deref(), Some("2.9.1"));
        let Entry::Response { id, usage, tools, text } = &line.entries[0] else { panic!() };
        assert_eq!(id, "msg_1");
        assert_eq!(usage.unwrap().context(), 51_002);
        assert_eq!(tools[0].command.as_deref(), Some("sleep 60"));
        assert!(tools[0].background);
        assert_eq!(text, &None);
    }

    #[test]
    fn a_usage_limit_is_an_api_error() {
        let raw = r#"{"type":"assistant","timestamp":"2026-10-27T00:17:27.276Z","error":"rate_limit","isApiErrorMessage":true,"message":{"id":"x","model":"<synthetic>","content":[{"type":"text","text":"You've hit your session limit · resets 1am (Europe/Lisbon)"}]}}"#;
        let line = parse_line(raw);
        assert_eq!(
            line.entries,
            vec![Entry::ApiError { error: "rate_limit".into(), text: "You've hit your session limit · resets 1am (Europe/Lisbon)".into() }]
        );
    }

    #[test]
    fn a_tool_result_is_reduced_to_flags() {
        let raw = r#"{"type":"user","timestamp":"2026-10-26T10:00:01Z","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_1","is_error":true,"content":"pre-push: tests/test_anonymity.py fails on refs/heads/x (abc); push refused. Scrub with placeholders first."}]}}"#;
        let line = parse_line(raw);
        let Entry::ToolResult { tool_use_id, is_error, flags } = &line.entries[0] else { panic!() };
        assert_eq!((tool_use_id.as_str(), *is_error, flags.push_refused, flags.home_guard), ("toolu_1", true, true, false));
        let flags = ResultFlags::of("X Release release/1.2.3 · 1 completed with 'failure'");
        assert_eq!((flags.run_succeeded, flags.mentions_release), (Some(false), true));
    }

    #[test]
    fn unknown_and_broken_lines_are_classed_not_fatal() {
        assert_eq!(parse_line("not json").class, LineClass::Unclassed);
        assert_eq!(parse_line(r#"{"type":"assistant","message":{}}"#).class, LineClass::Unclassed);
        assert_eq!(parse_line(r#"{"type":"attachment"}"#).class, LineClass::Skipped);
        assert_eq!(parse_line(r#"{"type":"brand-new"}"#).class, LineClass::UnknownType);
        let interrupted = parse_line(r#"{"type":"user","message":{"content":"[Request interrupted by user]"}}"#);
        assert_eq!(interrupted.entries, vec![Entry::UserText { interrupted: true }]);
    }
}
