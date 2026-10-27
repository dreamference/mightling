//! MCP tools as plain function tools, and Code Mode's `exec` as one where the server needs it.
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
//!
//! The same two places carry Code Mode's `exec` to a server that drops custom tools
//! ([`custom`]): [`for_server`], which the request hook calls in place of [`flatten_raw`], also
//! offers `exec` as a function and replays its earlier calls as function calls, and
//! [`from_server`] turns the model's call back into the custom tool call Codex expects.
//!
//! Patch 0027 calls [`for_request`] in place of [`for_server`]: the same, and a compaction
//! request offers the tools of its thread's last request ([`compaction`]), so its prompt shares
//! the cached prefix.

pub mod compaction;
pub mod custom;

pub use custom::custom_tools_dropped;
pub use custom::exec_as_function;
pub use custom::from_server;
pub use custom::set_custom_tools_dropped;
pub use custom::set_exec_as_function;

use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::OnceLock;

use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;
use serde_json::value::RawValue;

/// Namespaces that are MCP servers; Codex's own namespaces (`multi_agent_v1`, …) are left as
/// they are.
const MCP_PREFIX: &str = "mcp__";

/// What Codex appends to an MCP tool's description for Code Mode: the tool's JavaScript binding
/// inside `exec`. Kept when the model can see `exec`, left out when it cannot.
const CODE_MODE_SUFFIX: &str = "\n\nexec tool declaration:";

/// The longest server `instructions` carried to the model, in characters. An MCP server sends
/// them in its `initialize` reply and Codex puts them in the namespace's description, which
/// flattening drops; they are put back on the server's first tool, bounded so a server cannot
/// fill the prompt.
pub const INSTRUCTIONS_MAX_CHARS: usize = 4_000;

/// Flattened name → (namespace, tool name), filled by [`flatten`] and read by [`resolve`].
fn names() -> &'static Mutex<HashMap<String, (String, String)>> {
    static NAMES: OnceLock<Mutex<HashMap<String, (String, String)>>> = OnceLock::new();
    NAMES.get_or_init(Mutex::default)
}

/// Replaces every `mcp__…` namespace in a request's tools with its tools, as functions, offers
/// `exec` as a function when this process translates it, and leaves out Code Mode's `wait` when
/// the model cannot see `exec` ([`custom`]).
pub fn flatten(tools: Vec<Value>) -> Vec<Value> {
    flatten_with(tools, exec_as_function(), custom_tools_dropped())
}

/// [`flatten`], with `exec` offered as a function when `exec_as_function` is true, on a server
/// that drops custom tools when `custom_tools_dropped` is.
pub fn flatten_with(tools: Vec<Value>, exec_as_function: bool, custom_tools_dropped: bool) -> Vec<Value> {
    let orphaned: Vec<bool> =
        tools.iter().map(|tool| custom::is_orphaned_wait(tool, &tools, exec_as_function, custom_tools_dropped)).collect();
    let mut taken: HashSet<String> = tools
        .iter()
        .filter(|tool| tool["type"] != "namespace")
        .filter_map(|tool| tool["name"].as_str().map(str::to_string))
        .collect();
    let exec_visible = tools.iter().any(|tool| exec_offered(tool, exec_as_function, custom_tools_dropped));
    let mut flat = Vec::with_capacity(tools.len());
    let mut map = names().lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    for (tool, orphaned) in tools.into_iter().zip(orphaned) {
        if orphaned {
            continue;
        }
        let namespace = tool["name"].as_str().unwrap_or_default().to_string();
        let inner = match &tool["tools"] {
            Value::Array(inner) if tool["type"] == "namespace" && namespace.starts_with(MCP_PREFIX) => inner.clone(),
            _ if exec_as_function && custom::is_translated(&tool) => {
                flat.push(custom::as_function(&tool));
                continue;
            }
            _ => {
                flat.push(tool);
                continue;
            }
        };
        let mut instructions = server_instructions(&tool, &namespace);
        for mut function in inner {
            let Some(name) = function["name"].as_str().map(str::to_string) else { continue };
            if function["type"] != "function" {
                continue;
            }
            let shown = if taken.contains(&name) { format!("{namespace}__{name}") } else { name.clone() };
            if !exec_visible
                && let Some(description) = function["description"].as_str()
                && let Some(end) = description.find(CODE_MODE_SUFFIX)
            {
                function["description"] = Value::String(description[..end].to_string());
            }
            if let Some(instructions) = instructions.take() {
                let description = function["description"].as_str().unwrap_or_default();
                function["description"] = Value::String(if description.is_empty() {
                    instructions
                } else {
                    format!("{instructions}\n\n{description}")
                });
            }
            function["name"] = Value::String(shown.clone());
            taken.insert(shown.clone());
            map.insert(shown, (namespace.clone(), name));
            flat.push(function);
        }
    }
    flat
}

/// Whether the model can call `exec` through `tool`: Codex's custom tool where the server keeps
/// custom tools or this process offers it as a function ([`custom`]), or a function already.
fn exec_offered(tool: &Value, exec_as_function: bool, custom_tools_dropped: bool) -> bool {
    tool["name"] == "exec"
        && (tool["type"] == "function" || (custom::is_translated(tool) && (exec_as_function || !custom_tools_dropped)))
}

/// The instructions an MCP namespace carries in its description, bounded to
/// [`INSTRUCTIONS_MAX_CHARS`]; none when it holds only Codex's default text
/// ("Tools in the <namespace> namespace.") or nothing.
fn server_instructions(namespace_tool: &Value, namespace: &str) -> Option<String> {
    let text = namespace_tool["description"].as_str()?.trim();
    if text.is_empty() || text == format!("Tools in the {namespace} namespace.") {
        return None;
    }
    if text.chars().count() <= INSTRUCTIONS_MAX_CHARS {
        return Some(text.to_string());
    }
    let mut bounded: String = text.chars().take(INSTRUCTIONS_MAX_CHARS).collect();
    bounded.push_str(" […]");
    Some(bounded)
}

/// [`flatten`] on the raw JSON array Codex sends as a request's `tools`. A list with no MCP
/// namespace, or one that does not parse, is returned as it came (the same allocation, so
/// Codex's request comparison still sees it unchanged).
pub fn flatten_raw(tools: Arc<RawValue>) -> Arc<RawValue> {
    let custom = (exec_as_function() || custom_tools_dropped()) && tools.get().contains("\"custom\"");
    if !tools.get().contains(MCP_PREFIX) && !custom {
        return tools;
    }
    let Ok(list) = serde_json::from_str::<Vec<Value>>(tools.get()) else { return tools };
    match serde_json::value::to_raw_value(&flatten(list)) {
        Ok(raw) => Arc::from(raw),
        Err(_) => tools,
    }
}

/// What the request hook (patch 0020) calls on every request: the tools through [`flatten_raw`],
/// and, when this process offers `exec` as a function, the input's earlier `exec` calls and their
/// outputs as the function calls the model saw ([`custom::replay`]).
pub fn for_server<T: Serialize + DeserializeOwned>(tools: Arc<RawValue>, input: &mut [T]) -> Arc<RawValue> {
    if exec_as_function() {
        custom::replay(input);
    }
    flatten_raw(tools)
}

/// What the request hook calls since patch 0027: [`for_server`], then [`compaction::same_tools`]
/// with the request's thread id and whether it is a compaction.
pub fn for_request<T: Serialize + DeserializeOwned>(
    tools: Arc<RawValue>,
    input: &mut [T],
    thread: &str,
    compaction: bool,
) -> Arc<RawValue> {
    compaction::same_tools(for_server(tools, input), thread, compaction)
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
            json!({"type": "namespace", "name": "mcp__mightling_code", "description": "Tools in the mcp__mightling_code namespace.", "tools": [
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

    fn server(description: &str) -> Vec<Value> {
        vec![json!({"type": "namespace", "name": "mcp__docs", "description": description, "tools": [
            {"type": "function", "name": "docs_search", "description": "Searches.\n\nexec tool declaration:\n```ts\n…```"},
            {"type": "function", "name": "docs_read", "description": "Reads."},
        ]})]
    }

    #[test]
    fn the_servers_instructions_reach_the_model_on_its_first_tool_only() {
        let flat = flatten_with(server("Search before you read."), false, false);
        assert_eq!(flat[0]["description"], "Search before you read.\n\nSearches.");
        assert_eq!(flat[1]["description"], "Reads.");
        // Codex's default description is not instructions.
        let flat = flatten_with(server("Tools in the mcp__docs namespace."), false, false);
        assert_eq!(flat[0]["description"], "Searches.");
    }

    #[test]
    fn long_instructions_are_bounded() {
        let long = "é".repeat(INSTRUCTIONS_MAX_CHARS + 500);
        let flat = flatten_with(server(&long), false, false);
        let description = flat[0]["description"].as_str().unwrap_or_default();
        assert!(description.starts_with(&"é".repeat(INSTRUCTIONS_MAX_CHARS)));
        assert!(description.ends_with(" […]\n\nSearches."));
        assert_eq!(description.chars().count(), INSTRUCTIONS_MAX_CHARS + " […]\n\nSearches.".chars().count());
    }

    #[test]
    fn the_exec_declaration_stays_when_the_model_can_call_exec() {
        let exec = json!({"type": "custom", "name": "exec", "description": "Runs JavaScript."});
        let with_exec = |exec_as_function, custom_tools_dropped| {
            let mut tools = vec![exec.clone()];
            tools.extend(server(""));
            flatten_with(tools, exec_as_function, custom_tools_dropped)
                .into_iter()
                .find(|tool| tool["name"] == "docs_search")
                .map(|tool| tool["description"].as_str().unwrap_or_default().to_string())
                .unwrap_or_default()
        };
        // The server keeps custom tools, or this process offers exec as a function: exec is there.
        assert!(with_exec(false, false).contains("exec tool declaration:"));
        assert!(with_exec(true, true).contains("exec tool declaration:"));
        // The server drops custom tools and nothing translates exec: the binding is left out.
        assert_eq!(with_exec(false, true), "Searches.");
    }

    #[test]
    fn flattening_twice_gives_the_same_request() {
        // Every turn sends the tools again; the result must not drift (it is part of the cached
        // prompt prefix).
        assert_eq!(flatten(request()), flatten(request()));
    }
}
