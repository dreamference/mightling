//! `mling-code mcp`: the same operations over MCP (stdio, JSON-RPC 2.0, one message per line)
//! for Claude Code and IDEs (spec §8). Each call loads the stores afresh, as a shell query does, so
//! it never holds a handle across a re-index; the answers are the same text the agent reads.

use std::io::{BufRead, Write};

use anyhow::Result;
use serde_json::{json, Value};

use crate::config::Settings;
use crate::output::{self, Page};
use crate::paths::Repo;
use crate::router::Context;

// Short, because every request carries them: the nine schemas were most of the index's 2.1K
// tokens of fixed overhead (specs/DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md §4.3). When to use which
// tool is the prompt block's job (prompt.rs).
const TOOLS: &[(&str, &str, &str)] = &[
    ("search", "words", "Find definitions by topic when you know no name (a bug report); use before grep."),
    ("def", "name", "Where a name is defined. Qualified (Circle.area) or path:line."),
    ("show", "name", "A definition's source with line numbers, 100 lines a page (offset for more)."),
    ("refs", "name", "Every use of a definition. Run before renaming or changing a signature."),
    ("callers", "name", "What calls a definition."),
    ("callees", "name", "What a definition calls."),
    ("impl", "name", "Implementations of a trait, interface or method."),
    ("impact", "name", "What breaks if a definition changes, three levels deep."),
    ("outline", "file", "A file's definitions and line ranges; use before reading a file."),
    ("status", "", "Index layers, freshness and exclusions."),
];

/// The answer to `tools/list`.
///
/// Every tool only reads, and says so: Codex runs a tool marked read-only without asking, and
/// asks for any other, which `mling exec` (approval policy `never`) turns into a refusal of the
/// call ("MCP tool call requires approval", measured 2026-10-02).
fn tools_list() -> Value {
    json!({ "tools": TOOLS.iter().map(|(name, arg, description)| {
        let schema = if arg.is_empty() {
            json!({ "type": "object", "properties": {} })
        } else {
            json!({ "type": "object", "properties": {
                *arg: { "type": "string" },
                "limit": { "type": "integer" },
                "offset": { "type": "integer" },
                "path": { "type": "string" }
            }, "required": [arg] })
        };
        json!({ "name": format!("code_{name}"), "description": description, "inputSchema": schema,
                "annotations": { "readOnlyHint": true } })
    }).collect::<Vec<_>>() })
}

pub fn serve(repo: Repo, settings: Settings) -> Result<()> {
    let stdin = std::io::stdin();
    let mut stdout = std::io::stdout();
    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let Ok(message) = serde_json::from_str::<Value>(&line) else { continue };
        let Some(id) = message.get("id").cloned() else { continue }; // a notification
        let method = message.get("method").and_then(Value::as_str).unwrap_or("");
        let result = match method {
            "initialize" => Ok(json!({
                "protocolVersion": message["params"]["protocolVersion"].as_str().unwrap_or("2025-06-18"),
                "capabilities": { "tools": {} },
                "serverInfo": { "name": "mling-code", "version": env!("CARGO_PKG_VERSION") }
            })),
            "tools/list" => Ok(tools_list()),
            "tools/call" => call(&repo, &settings, &message["params"]).map(|text| json!({ "content": [{ "type": "text", "text": text }] })),
            "ping" => Ok(json!({})),
            _ => Err(anyhow::anyhow!("method not found: {method}")),
        };
        let response = match result {
            Ok(result) => json!({ "jsonrpc": "2.0", "id": id, "result": result }),
            Err(error) => json!({ "jsonrpc": "2.0", "id": id, "error": { "code": -32601, "message": error.to_string() } }),
        };
        writeln!(stdout, "{response}")?;
        stdout.flush()?;
    }
    Ok(())
}

fn call(repo: &Repo, settings: &Settings, params: &Value) -> Result<String> {
    let name = params["name"].as_str().unwrap_or("").trim_start_matches("code_");
    let args = &params["arguments"];
    let text = |key: &str| args[key].as_str().unwrap_or("").to_string();
    let context = Context::load(repo.clone(), settings.clone())?;
    if name == "status" {
        return Ok(context.status().join("\n"));
    }
    if !context.has_index() {
        return Ok("no code index for this repository yet; run `mling-code index`".to_string());
    }
    let (mut answer, body) = match name {
        "def" => (context.def(&text("name"))?, None),
        "refs" => (context.refs(&text("name"))?, None),
        "callers" => (context.callers(&text("name"))?, None),
        "callees" => (context.callees(&text("name"))?, None),
        "impl" => (context.implementations(&text("name"))?, None),
        "impact" => (context.impact(&text("name"), crate::router::IMPACT_DEPTH)?, None),
        "outline" => (context.outline(&text("file"))?, None),
        "search" => (context.search(&text("words"))?, None),
        "show" => context.show(&text("name"))?,
        other => anyhow::bail!("unknown tool {other}"),
    };
    let page = Page {
        limit: args["limit"].as_u64().map(|v| v as usize).unwrap_or(if body.is_some() { output::SHOW_LINES } else { settings.row_limit }),
        offset: args["offset"].as_u64().unwrap_or(0) as usize,
        path: args["path"].as_str().map(|path| output::repository_relative(path, &repo.root)),
        ..Page::default()
    };
    output::narrow(&mut answer, &page)?;
    Ok(output::render(&answer, &page, body.as_deref()))
}

#[cfg(test)]
mod tests {
    #[test]
    fn every_tool_is_read_only_and_says_when_to_use_it() {
        let list = super::tools_list();
        let tools = list["tools"].as_array().unwrap();
        assert_eq!(tools.len(), super::TOOLS.len());
        for tool in tools {
            assert_eq!(tool["annotations"]["readOnlyHint"], true, "{tool}");
            assert!(tool["name"].as_str().unwrap().starts_with("code_"));
        }
        // `search` leads: it is where a task that names no symbol starts.
        assert_eq!(tools[0]["name"], "code_search");
        assert!(tools[0]["description"].as_str().unwrap().contains("before grep"));
    }
}
