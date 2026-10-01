//! `puffin-code mcp`: the same operations over MCP (stdio, JSON-RPC 2.0, one message per line)
//! for Claude Code and IDEs (spec §8). Each call loads the stores afresh, as a shell query does, so
//! it never holds a handle across a re-index; the answers are the same text the agent reads.

use std::io::{BufRead, Write};

use anyhow::Result;
use serde_json::{json, Value};

use crate::config::Settings;
use crate::output::{self, Page};
use crate::paths::Repo;
use crate::router::Context;

const TOOLS: &[(&str, &str, &str)] = &[
    ("def", "name", "Where a name is defined. Names may be qualified (Circle.area) or path:line."),
    ("refs", "name", "Every reference to a definition, tagged exact / heuristic / heuristic (text)."),
    ("callers", "name", "The definitions that refer to a definition."),
    ("callees", "name", "What a definition's body refers to."),
    ("impl", "name", "Implementations of a trait, interface or method."),
    ("impact", "name", "What breaks if a definition changes: its references, then theirs, three levels deep."),
    ("show", "name", "One definition's source."),
    ("outline", "file", "The definitions of a file."),
    ("search", "words", "Definitions whose name or body matches the words."),
    ("status", "", "Which index layers exist, how fresh they are, and what is excluded."),
];

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
                "serverInfo": { "name": "puffin-code", "version": env!("CARGO_PKG_VERSION") }
            })),
            "tools/list" => Ok(json!({ "tools": TOOLS.iter().map(|(name, arg, description)| {
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
                json!({ "name": format!("code_{name}"), "description": description, "inputSchema": schema })
            }).collect::<Vec<_>>() })),
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
        return Ok("no code index for this repository yet; run `puffin-code index`".to_string());
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
        limit: args["limit"].as_u64().map(|v| v as usize).unwrap_or(settings.row_limit),
        offset: args["offset"].as_u64().unwrap_or(0) as usize,
        path: args["path"].as_str().map(str::to_string),
        ..Page::default()
    };
    output::narrow(&mut answer, &page)?;
    Ok(output::render(&answer, &page, body.as_deref()))
}
