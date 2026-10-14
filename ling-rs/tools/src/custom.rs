//! Code Mode's `exec` as a function tool, for a model server that drops custom tools.
//!
//! Codex offers Code Mode's `exec` as a Responses `{"type": "custom"}` tool (free text checked
//! against a Lark grammar) and expects the call back as a `custom_tool_call` item. SGLang and vLLM
//! render only function tools into the model's prompt and drop the rest without a word, so on
//! them the model never saw `exec` (measured 2026-10-10: eleven turns of a Code Mode session, no
//! call). ling-engine translates on its side (its API spec §4). Since 2026-10-12 `ling` translates
//! on every other server the same way, so Code Mode works on today's SGLang:
//!
//! - the tool is offered as a function with the one string parameter `input`, its description
//!   followed by the grammar as prose, in exactly the words ling-engine uses ([`as_function`]);
//! - the model's `function_call` to it becomes the `custom_tool_call` Codex expects, before Codex
//!   records it, so the session's history is what it is against ling-engine ([`from_server`]);
//! - an earlier call and its output are sent back as the function call and output the model saw
//!   ([`replay`]): `custom_tool_call` as a `function_call` with `{"input": …}` arguments,
//!   `custom_tool_call_output` as a `function_call_output` whose text is its parts' text joined,
//!   as ling-engine renders it.
//!
//! Only the tools in [`TRANSLATED`] are translated: `apply_patch`'s freeform form keeps its own
//! switch in the launcher (`DREAMFERENCE_MIGHTLING_APPLY_PATCH`). The launcher decides once per
//! process whether to translate ([`set_exec_as_function`]), from `/v1/models`' `owned_by` and
//! `DREAMFERENCE_MIGHTLING_EXEC_TOOL`; nothing changes when it does not.
//!
//! Code Mode's other tool, `wait`, is a function, so it reaches the model on every server. Where
//! `exec` stays custom and the server drops it, `wait` is offered alone: it can only name a cell
//! `exec` started, and no cell can exist. The model then passed it `exec_command`'s session and
//! chunk ids ("exec cell … not found"): 424 calls in the SWE-bench runs of 2026-10-08 to 10-12 and
//! the compaction bench of 2026-10-13, every one failed; in the bench the model then relaunched
//! its test run instead of polling it with `write_stdin`. That `wait` is taken out of the request
//! ([`is_orphaned_wait`], [`set_custom_tools_dropped`]).

use std::collections::HashSet;
use std::sync::Mutex;
use std::sync::OnceLock;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;

use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;
use serde_json::json;

/// The custom tools offered as functions: Code Mode's `exec` only.
pub const TRANSLATED: &[&str] = &["exec"];

/// The one parameter a translated tool takes, as ling-engine names it.
pub const INPUT: &str = "input";

/// The parameter's description, as ling-engine words it.
const INPUT_DESCRIPTION: &str = "The tool's input, as free text";

static EXEC_AS_FUNCTION: AtomicBool = AtomicBool::new(false);

/// Sets, for this process, whether `exec` is offered as a function (the launcher, before Codex
/// sends anything).
pub fn set_exec_as_function(on: bool) {
    EXEC_AS_FUNCTION.store(on, Ordering::Relaxed);
}

/// Whether `exec` is offered as a function in this process.
pub fn exec_as_function() -> bool {
    EXEC_AS_FUNCTION.load(Ordering::Relaxed)
}

/// Code Mode's `wait`: a function that waits on a cell `exec` started.
pub const WAIT: &str = "wait";

static CUSTOM_TOOLS_DROPPED: AtomicBool = AtomicBool::new(false);

/// Sets, for this process, whether the model server drops custom tools (SGLang, vLLM; not
/// ling-engine). The launcher sets it with [`set_exec_as_function`].
pub fn set_custom_tools_dropped(on: bool) {
    CUSTOM_TOOLS_DROPPED.store(on, Ordering::Relaxed);
}

/// Whether the model server drops custom tools, in this process.
pub fn custom_tools_dropped() -> bool {
    CUSTOM_TOOLS_DROPPED.load(Ordering::Relaxed)
}

/// Whether `tool` is Code Mode's `wait` in a request whose `exec` never reaches the model:
/// `tools` holds `exec` as a custom tool, it is not offered as a function, and the server drops
/// custom tools.
pub fn is_orphaned_wait(tool: &Value, tools: &[Value], exec_as_function: bool, custom_tools_dropped: bool) -> bool {
    !exec_as_function
        && custom_tools_dropped
        && tool["type"] == "function"
        && tool["name"] == WAIT
        && tools.iter().any(is_translated)
}

/// The names offered as functions so far in this process: only a call to one of these is turned
/// back into a custom tool call.
fn offered() -> &'static Mutex<HashSet<String>> {
    static OFFERED: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    OFFERED.get_or_init(Mutex::default)
}

/// Whether `tool` is a custom tool this module translates.
pub fn is_translated(tool: &Value) -> bool {
    tool["type"] == "custom" && tool["name"].as_str().is_some_and(|name| TRANSLATED.contains(&name))
}

/// A custom tool as the function ling-engine offers in its place: the one string parameter
/// [`INPUT`], and the description followed by the grammar from `format`.
pub fn as_function(tool: &Value) -> Value {
    let name = tool["name"].as_str().unwrap_or_default();
    let mut description = tool["description"].as_str().unwrap_or_default().to_string();
    let format = &tool["format"];
    if format["type"] == "grammar"
        && let Some(definition) = format["definition"].as_str()
    {
        let syntax = format["syntax"].as_str().unwrap_or_default();
        if !description.is_empty() {
            description.push_str("\n\n");
        }
        description.push_str("The input must follow this ");
        if !syntax.is_empty() {
            description.push_str(syntax);
            description.push(' ');
        }
        description.push_str("grammar:\n");
        description.push_str(definition);
    }
    offered().lock().unwrap_or_else(|poisoned| poisoned.into_inner()).insert(name.to_string());
    json!({
        "type": "function",
        "name": name,
        "description": description,
        "strict": false,
        "parameters": {
            "type": "object",
            "properties": {INPUT: {"type": "string", "description": INPUT_DESCRIPTION}},
            "required": [INPUT],
        },
    })
}

/// The custom tool's input from a function call's arguments, as ling-engine reads it: the
/// [`INPUT`] parameter; failing that, no parameters at all is an empty input and one string
/// parameter of another name is taken as the input; failing that, the arguments text itself.
pub fn input_of(arguments: &str) -> String {
    if let Ok(Value::Object(args)) = serde_json::from_str::<Value>(arguments) {
        if let Some(Value::String(input)) = args.get(INPUT) {
            return input.clone();
        }
        if args.is_empty() {
            return String::new();
        }
        if args.len() == 1
            && let Some(Value::String(input)) = args.values().next()
        {
            return input.clone();
        }
    }
    arguments.to_string()
}

/// One output item from the server: a `function_call` to a tool offered as a function becomes the
/// `custom_tool_call` Codex expects. Anything else, and every item when the translation is off,
/// comes back as it came.
pub fn from_server<T: Serialize + DeserializeOwned>(item: T) -> T {
    if !exec_as_function() {
        return item;
    }
    let Ok(value) = serde_json::to_value(&item) else { return item };
    match custom_call(&value) {
        Some(custom) => serde_json::from_value(custom).unwrap_or(item),
        None => item,
    }
}

/// [`from_server`] on JSON.
pub fn custom_call(item: &Value) -> Option<Value> {
    let name = item["name"].as_str()?;
    if item["type"] != "function_call" || !item["namespace"].is_null() {
        return None;
    }
    if !offered().lock().unwrap_or_else(|poisoned| poisoned.into_inner()).contains(name) {
        return None;
    }
    let mut custom = json!({
        "type": "custom_tool_call",
        "call_id": item["call_id"],
        "name": name,
        "input": input_of(item["arguments"].as_str().unwrap_or_default()),
    });
    if let Some(id) = item.get("id").filter(|id| !id.is_null()) {
        custom["id"] = id.clone();
    }
    Some(custom)
}

/// The request's input: each earlier call to a translated tool, and its output, as the function
/// call and output the model saw. Items that are not changed are left untouched.
pub fn replay<T: Serialize + DeserializeOwned>(items: &mut [T]) {
    let values: Vec<Value> = items.iter().map(|item| serde_json::to_value(item).unwrap_or(Value::Null)).collect();
    for (index, replayed) in replay_values(&values) {
        if let Ok(item) = serde_json::from_value(replayed) {
            items[index] = item;
        }
    }
}

/// [`replay`] on JSON: the index and new form of each item that changes.
pub fn replay_values(items: &[Value]) -> Vec<(usize, Value)> {
    let calls: HashSet<&str> = items
        .iter()
        .filter(|item| item["type"] == "custom_tool_call" && item["namespace"].is_null())
        .filter(|item| item["name"].as_str().is_some_and(|name| TRANSLATED.contains(&name)))
        .filter_map(|item| item["call_id"].as_str())
        .collect();
    let mut changed = Vec::new();
    for (index, item) in items.iter().enumerate() {
        let Some(call_id) = item["call_id"].as_str().filter(|id| calls.contains(id)) else { continue };
        let mut replayed = if item["type"] == "custom_tool_call" {
            let mut arguments = serde_json::Map::new();
            arguments.insert(INPUT.to_string(), Value::String(item["input"].as_str().unwrap_or_default().to_string()));
            json!({
                "type": "function_call",
                "name": item["name"],
                "arguments": Value::Object(arguments).to_string(),
                "call_id": call_id,
            })
        } else if item["type"] == "custom_tool_call_output" {
            json!({"type": "function_call_output", "call_id": call_id, "output": output_text(&item["output"])})
        } else {
            continue;
        };
        if let Some(id) = item.get("id").filter(|id| !id.is_null()) {
            replayed["id"] = id.clone();
        }
        changed.push((index, replayed));
    }
    changed
}

/// A call's output for a `function_call_output`: a list of text parts is their text joined, as
/// ling-engine renders a tool message; a list holding anything else (an image) is kept whole, as
/// Codex sends `view_image`'s output; a string is kept.
fn output_text(output: &Value) -> Value {
    match output {
        Value::Array(parts) if parts.iter().all(|part| part["text"].is_string()) => {
            Value::String(parts.iter().filter_map(|part| part["text"].as_str()).collect())
        }
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::flatten_with;

    /// Excerpts of the recorded sessions of 2026-10-10 (`fixtures/code_mode_2026_10_10.json`).
    fn recorded() -> Value {
        serde_json::from_str(include_str!("../fixtures/code_mode_2026_10_10.json")).unwrap_or(Value::Null)
    }

    fn list(value: &Value) -> Vec<Value> {
        value.as_array().cloned().unwrap_or_default()
    }

    #[test]
    fn the_recorded_exec_tool_is_offered_as_ling_engines_function() {
        let tools = list(&recorded()["production_tools"]);
        let exec = &tools[0];
        assert_eq!((exec["type"].as_str(), exec["name"].as_str()), (Some("custom"), Some("exec")));
        let sent = flatten_with(tools.clone(), true, true);
        // The custom tool is now a function with one string parameter; the description is Codex's
        // followed by the grammar, worded as ling-engine words it (its API spec §4).
        let function = &sent[0];
        assert_eq!(function["type"], "function");
        assert_eq!(function["name"], "exec");
        let description = function["description"].as_str().unwrap_or_default();
        let expected = format!(
            "{}\n\nThe input must follow this lark grammar:\n{}",
            exec["description"].as_str().unwrap_or_default(),
            exec["format"]["definition"].as_str().unwrap_or_default()
        );
        assert_eq!(description, expected);
        assert_eq!(
            function["parameters"],
            json!({"type": "object", "properties": {"input": {"type": "string", "description": "The tool's input, as free text"}}, "required": ["input"]})
        );
        // The other tools are sent as they came.
        assert_eq!(sent[1..], tools[1..]);
        // Off on ling-engine (which runs custom tools), nothing changes: `exec` and `wait` both.
        assert_eq!(flatten_with(tools.clone(), false, false), tools);
        // Every request sends the tools again: the result must not drift (cached prefix).
        assert_eq!(flatten_with(tools.clone(), true, true), sent);
    }

    #[test]
    fn wait_is_not_offered_without_an_exec_the_model_can_see() {
        // The recorded tools are Codex's two Code Mode tools: `exec` (custom) and `wait` (function).
        let tools = list(&recorded()["production_tools"]);
        assert_eq!((tools[1]["type"].as_str(), tools[1]["name"].as_str()), (Some("function"), Some(WAIT)));
        // SGLang with `exec` left custom (the switch's `freeform`): the server drops `exec`, so
        // `wait` goes too. Every `wait` sent this way failed ("exec cell 83043 not found": the
        // model passed it `exec_command`'s session id).
        let sent = flatten_with(tools.clone(), false, true);
        assert_eq!(sent, tools[..1]);
        assert_eq!(flatten_with(tools.clone(), false, true), sent);
        // `exec` as a function on the same server: `wait` has its cells, and stays.
        let sent = flatten_with(tools.clone(), true, true);
        assert_eq!(sent.iter().map(|tool| tool["name"].as_str()).collect::<Vec<_>>(), [Some("exec"), Some(WAIT)]);
        // A `wait` with no Code Mode `exec` in the request is not Code Mode's: left alone.
        let other = vec![json!({"type": "function", "name": "wait", "description": "Sleeps"})];
        assert_eq!(flatten_with(other.clone(), false, true), other);
        // Nor is a `wait` inside a namespace (`multi_agent_v1`'s `wait_agent` lives there).
        let mut nested = tools.clone();
        nested[1] = json!({"type": "namespace", "name": "multi_agent_v1", "tools": [tools[1].clone()]});
        assert_eq!(flatten_with(nested.clone(), false, true), nested);
    }

    #[test]
    fn a_function_call_to_exec_comes_back_as_the_custom_call_ling_engine_returned() {
        let recorded = recorded();
        flatten_with(list(&recorded["production_tools"]), true, true);
        let engine = &recorded["engine_output_item"];
        // What SGLang returns for the call ling-engine returned as a custom tool call.
        let arguments = json!({"input": engine["input"]}).to_string();
        let from_sglang = json!({
            "type": "function_call", "id": engine["id"], "call_id": engine["call_id"], "name": "exec",
            "arguments": arguments, "status": "completed",
        });
        let mut expected = engine.clone();
        expected.as_object_mut().map(|item| item.remove("status"));
        assert_eq!(custom_call(&from_sglang), Some(expected));
        // A call to any other tool is left alone: SGLang's recorded exec_command call.
        assert_eq!(custom_call(&recorded["production_output_item"]), None);
        // A namespaced call is an MCP tool's, never exec.
        let mut namespaced = from_sglang.clone();
        namespaced["namespace"] = json!("mcp__x");
        assert_eq!(custom_call(&namespaced), None);
    }

    #[test]
    fn the_input_is_read_from_the_arguments_as_ling_engine_reads_it() {
        assert_eq!(input_of(r#"{"input": "text('hi')"}"#), "text('hi')");
        assert_eq!(input_of(r#"{"code": "text('hi')"}"#), "text('hi')");
        assert_eq!(input_of("{}"), "");
        assert_eq!(input_of(r#"{"a": "x", "b": "y"}"#), r#"{"a": "x", "b": "y"}"#);
        assert_eq!(input_of("text('hi')"), "text('hi')");
    }

    #[test]
    fn the_recorded_history_is_replayed_as_the_function_calls_the_model_saw() {
        let recorded = recorded();
        let mut input = list(&recorded["engine_replayed_input"]);
        let untouched = list(&recorded["production_replayed_input"]);
        input.extend(untouched.clone());
        let changed = replay_values(&input);
        assert_eq!(changed.iter().map(|(index, _)| *index).collect::<Vec<_>>(), [0, 1]);
        let call = &changed[0].1;
        assert_eq!(call["type"], "function_call");
        assert_eq!(call["name"], "exec");
        assert_eq!(call["call_id"], input[0]["call_id"]);
        assert_eq!(call["id"], input[0]["id"]);
        let arguments: Value = serde_json::from_str(call["arguments"].as_str().unwrap_or_default()).unwrap_or(Value::Null);
        assert_eq!(arguments, json!({"input": input[0]["input"]}));
        // The output's text parts joined, as ling-engine renders the tool message.
        let output = &changed[1].1;
        assert_eq!(output["type"], "function_call_output");
        assert_eq!(output["call_id"], input[1]["call_id"]);
        assert_eq!(output["output"], "Script completed\nWall time 0.0 seconds\nOutput:\nhello");
        // Replaying what was replayed changes nothing more.
        let mut again = input.clone();
        for (index, item) in changed {
            again[index] = item;
        }
        assert!(replay_values(&again).is_empty());
    }

    #[test]
    fn an_output_with_an_image_is_kept_whole_and_other_custom_tools_are_left_alone() {
        let items = vec![
            json!({"type": "custom_tool_call", "call_id": "a", "name": "exec", "input": "image(x)"}),
            json!({"type": "custom_tool_call_output", "call_id": "a", "output": [
                {"type": "input_text", "text": "Script completed"},
                {"type": "input_image", "image_url": "data:image/png;base64,AA"},
            ]}),
            // apply_patch's freeform form keeps its own switch: never translated here.
            json!({"type": "custom_tool_call", "call_id": "b", "name": "apply_patch", "input": "*** Begin Patch"}),
            json!({"type": "custom_tool_call_output", "call_id": "b", "output": "Done"}),
        ];
        let changed = replay_values(&items);
        assert_eq!(changed.len(), 2);
        assert_eq!(changed[1].1["output"], items[1]["output"]);
    }

    /// A stand-in for Codex's `ResponseItem`: the hooks only round-trip it through JSON.
    #[derive(Debug, PartialEq, serde::Serialize, serde::Deserialize)]
    #[serde(tag = "type", rename_all = "snake_case")]
    enum Item {
        FunctionCall { name: String, arguments: String, call_id: String },
        CustomToolCall { name: String, input: String, call_id: String },
        CustomToolCallOutput { call_id: String, output: Value },
        FunctionCallOutput { call_id: String, output: Value },
        Message { role: String },
    }

    #[test]
    fn the_hooks_round_trip_codexs_items_and_do_nothing_when_off() {
        // The only test that sets the process's switch; the others pass it explicitly.
        flatten_with(vec![json!({"type": "custom", "name": "exec", "description": "Run"})], true, true);
        let call = || Item::FunctionCall { name: "exec".into(), arguments: r#"{"input":"text(1)"}"#.into(), call_id: "c".into() };
        set_exec_as_function(false);
        assert_eq!(from_server(call()), call());
        set_exec_as_function(true);
        let custom = from_server(call());
        assert_eq!(custom, Item::CustomToolCall { name: "exec".into(), input: "text(1)".into(), call_id: "c".into() });
        let mut input = vec![
            Item::Message { role: "user".into() },
            custom,
            Item::CustomToolCallOutput { call_id: "c".into(), output: json!("1") },
        ];
        replay(&mut input);
        set_exec_as_function(false);
        assert_eq!(input[0], Item::Message { role: "user".into() });
        assert_eq!(input[1], call());
        assert_eq!(input[2], Item::FunctionCallOutput { call_id: "c".into(), output: json!("1") });
    }
}
