//! MCP tools as plain function tools.
//!
//! Codex declares the tools of an MCP server to the model as one `{"type": "namespace"}` entry
//! holding them, a tool type of OpenAI's Responses API. The local model servers (SGLang, vLLM)
//! render only `{"type": "function"}` entries into the model's prompt and drop the rest without a
//! word, so on this machine no MCP tool was ever visible to the model: none of 146 recorded
//! sessions called one, and a model asked to call `code_def` answered that no such tool exists
//! (measured 2026-10-02 by recording the requests `ling` sends). Declared as functions, the
//! same tools were the first thing the model reached for.
//!
//! Two hooks in Codex's core (patch 0020) call this module:
//!
//! - [`flatten_raw`] on the tools of every request (Codex keeps them as raw JSON): each tool of an `mcp__…` namespace becomes a
//!   function tool under its own name (`code_def`), or under `<namespace>__<name>` when that name
//!   is already taken;
//! - [`resolve`] on every function call the model makes: a flattened name gets its namespace
//!   back, which is how Codex finds the tool.
//!
//! History needs no rewriting: Codex sends an earlier call back as `name` plus a `namespace`
//! field the model server ignores, so the model sees the name it called.

use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::OnceLock;

use serde_json::Value;
use serde_json::value::RawValue;

/// Namespaces that are MCP servers; Codex's own namespaces (`multi_agent_v1`, …) are left as
/// they are.
const MCP_PREFIX: &str = "mcp__";

/// What Codex appends to an MCP tool's description for Code Mode; it describes a JavaScript
/// binding the model does not have here.
const CODE_MODE_SUFFIX: &str = "\n\nexec tool declaration:";

/// Flattened name → (namespace, tool name), filled by [`flatten`] and read by [`resolve`].
fn names() -> &'static Mutex<HashMap<String, (String, String)>> {
    static NAMES: OnceLock<Mutex<HashMap<String, (String, String)>>> = OnceLock::new();
    NAMES.get_or_init(Mutex::default)
}

/// Replaces every `mcp__…` namespace in a request's tools with its tools, as functions.
pub fn flatten(tools: Vec<Value>) -> Vec<Value> {
    let mut taken: HashSet<String> = tools
        .iter()
        .filter(|tool| tool["type"] != "namespace")
        .filter_map(|tool| tool["name"].as_str().map(str::to_string))
        .collect();
    let mut flat = Vec::with_capacity(tools.len());
    let mut map = names().lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    for tool in tools {
        let namespace = tool["name"].as_str().unwrap_or_default().to_string();
        let inner = match &tool["tools"] {
            Value::Array(inner) if tool["type"] == "namespace" && namespace.starts_with(MCP_PREFIX) => inner.clone(),
            _ => {
                flat.push(tool);
                continue;
            }
        };
        for mut function in inner {
            let Some(name) = function["name"].as_str().map(str::to_string) else { continue };
            if function["type"] != "function" {
                continue;
            }
            let shown = if taken.contains(&name) { format!("{namespace}__{name}") } else { name.clone() };
            if let Some(description) = function["description"].as_str()
                && let Some(end) = description.find(CODE_MODE_SUFFIX)
            {
                function["description"] = Value::String(description[..end].to_string());
            }
            function["name"] = Value::String(shown.clone());
            taken.insert(shown.clone());
            map.insert(shown, (namespace.clone(), name));
            flat.push(function);
        }
    }
    flat
}

/// [`flatten`] on the raw JSON array Codex sends as a request's `tools`. A list with no MCP
/// namespace, or one that does not parse, is returned as it came (the same allocation, so
/// Codex's request comparison still sees it unchanged).
pub fn flatten_raw(tools: Arc<RawValue>) -> Arc<RawValue> {
    if !tools.get().contains(MCP_PREFIX) {
        return tools;
    }
    let Ok(list) = serde_json::from_str::<Vec<Value>>(tools.get()) else { return tools };
    match serde_json::value::to_raw_value(&flatten(list)) {
        Ok(raw) => Arc::from(raw),
        Err(_) => tools,
    }
}

/// Gives a flattened tool its namespace back. A call that already names a namespace, or a name
/// [`flatten`] never produced, is returned as it came.
pub fn resolve(namespace: Option<String>, name: String) -> (Option<String>, String) {
    if namespace.is_some() {
        return (namespace, name);
    }
    let map = names().lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    match map.get(&name) {
        Some((namespace, tool)) => (Some(namespace.clone()), tool.clone()),
        None => (None, name),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn request() -> Vec<Value> {
        vec![
            json!({"type": "function", "name": "exec_command", "description": "Runs a command"}),
            json!({"type": "function", "name": "code_show", "description": "Already taken"}),
            json!({"type": "namespace", "name": "multi_agent_v1", "tools": [{"type": "function", "name": "wait_agent"}]}),
            json!({"type": "namespace", "name": "mcp__mightling_code", "description": "Tools in the namespace.", "tools": [
                {"type": "function", "name": "code_def", "description": "Where a name is defined.\n\nexec tool declaration:\n```ts\n…```", "parameters": {"type": "object"}},
                {"type": "function", "name": "code_show", "description": "One definition's source."},
            ]}),
            json!({"type": "web_search"}),
        ]
    }

    #[test]
    fn an_mcp_namespace_becomes_function_tools_and_the_rest_is_untouched() {
        let flat = flatten(request());
        let names: Vec<&str> = flat.iter().map(|tool| tool["name"].as_str().unwrap_or("-")).collect();
        assert_eq!(names, ["exec_command", "code_show", "multi_agent_v1", "code_def", "mcp__mightling_code__code_show", "-"]);
        // Codex's own namespace keeps its shape; the MCP tools are functions with their schema.
        assert_eq!(flat[2]["type"], "namespace");
        assert_eq!(flat[3]["type"], "function");
        assert_eq!(flat[3]["parameters"], json!({"type": "object"}));
        // The Code Mode binding is not part of what the model reads.
        assert_eq!(flat[3]["description"], "Where a name is defined.");
    }

    #[test]
    fn a_call_gets_its_namespace_back() {
        flatten(request());
        assert_eq!(resolve(None, "code_def".into()), (Some("mcp__mightling_code".into()), "code_def".into()));
        // A name that collided was declared with its namespace, and resolves from that.
        assert_eq!(
            resolve(None, "mcp__mightling_code__code_show".into()),
            (Some("mcp__mightling_code".into()), "code_show".into())
        );
        // Codex's own tools and already-namespaced calls pass through.
        assert_eq!(resolve(None, "exec_command".into()), (None, "exec_command".into()));
        assert_eq!(resolve(Some("multi_agent_v1".into()), "wait_agent".into()), (Some("multi_agent_v1".into()), "wait_agent".into()));
    }

    #[test]
    fn the_raw_list_codex_sends_is_flattened_and_a_plain_one_is_untouched() {
        let raw = |value: Value| Arc::from(serde_json::value::to_raw_value(&value).unwrap_or_else(|_| unreachable!()));
        let flat: Vec<Value> = serde_json::from_str(flatten_raw(raw(Value::Array(request()))).get()).unwrap_or_default();
        assert_eq!(flat, flatten(request()));
        let plain = raw(json!([{"type": "function", "name": "exec_command"}]));
        assert!(Arc::ptr_eq(&flatten_raw(plain.clone()), &plain));
    }

    #[test]
    fn flattening_twice_gives_the_same_request() {
        // Every turn sends the tools again; the result must not drift (it is part of the cached
        // prompt prefix).
        assert_eq!(flatten(request()), flatten(request()));
    }
}
