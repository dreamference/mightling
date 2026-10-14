//! Codex's custom tools as function tools, for a model server that drops custom tools: Code Mode's
//! `exec`, and `apply_patch` when a benchmark arm asks for its function form.
//!
//! Codex offers Code Mode's `exec` and `apply_patch` as Responses `{"type": "custom"}` tools (free
//! text checked against a Lark grammar) and expects the call back as a `custom_tool_call` item.
//! SGLang and vLLM render only function tools into the model's prompt and drop the rest without a
//! word, so on them the model never saw `exec` (measured 2026-10-10: eleven turns of a Code Mode
//! session, no call). ling-engine translates on its side (its API spec §4). Since 2026-10-12
//! `ling` translates on every other server the same way, so Code Mode works on today's SGLang:
//!
//! - the tool is offered as a function with the one string parameter `input`, its description
//!   followed by the grammar as prose, in exactly the words ling-engine uses ([`as_function`]);
//! - the model's `function_call` to it becomes the `custom_tool_call` Codex expects, before Codex
//!   records it, so the session's history is what it is against ling-engine and Codex's own
//!   handler runs it ([`from_server`]);
//! - an earlier call and its output are sent back as the function call and output the model saw
//!   ([`replay`]): `custom_tool_call` as a `function_call` with `{"input": …}` arguments,
//!   `custom_tool_call_output` as a `function_call_output` whose text is its parts' text joined,
//!   as ling-engine renders it.
//!
//! Each tool has its own switch, set once per process by the launcher before Codex sends anything:
//! `exec` from `/v1/models`' `owned_by` and `DREAMFERENCE_MIGHTLING_EXEC_TOOL`
//! ([`set_exec_as_function`]); `apply_patch` only when `DREAMFERENCE_MIGHTLING_APPLY_PATCH` is
//! `function` ([`set_apply_patch_as_function`]), the benchmark arm of SWE-bench night 8. Codex
//! 0.158 knows only the freeform `apply_patch` (`ApplyPatchToolType::Freeform`), so for that arm
//! the catalog asks for the freeform tool and this module offers it as a function: the patch text
//! arrives in `input` and Codex's freeform handler applies it. `apply_patch`'s function form says
//! what a function call needs ([`APPLY_PATCH_DESCRIPTION`]) in place of Codex's "do not wrap the
//! patch in JSON". Nothing changes for a tool whose switch is off.

use std::collections::HashSet;
use std::sync::Mutex;
use std::sync::OnceLock;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;

use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;
use serde_json::json;

/// Code Mode's tool.
pub const EXEC: &str = "exec";

/// Codex's patch tool, offered as a function only for a benchmark arm.
pub const APPLY_PATCH: &str = "apply_patch";

/// The one parameter a translated tool takes, as ling-engine names it (and as Codex's earlier JSON
/// `apply_patch` tool named it).
pub const INPUT: &str = "input";

/// The parameter's description, as ling-engine words it.
const INPUT_DESCRIPTION: &str = "The tool's input, as free text";

/// `apply_patch`'s description as a function. Codex's own, written for the freeform tool, tells the
/// model not to wrap the patch in JSON, which is what a function call does; the grammar follows it
/// as for every translated tool. The night script checks a `ling` carries it before an
/// `apply-patch-function` arm (`scripts/swe_bench_night1.sh`).
pub const APPLY_PATCH_DESCRIPTION: &str = "Edits files with a patch. Pass the whole patch as the `input` string, \
from `*** Begin Patch` to `*** End Patch`, one line of the patch per line of the string. Each file \
starts with `*** Add File: <path>` (every line after it starts with `+`), `*** Delete File: <path>`, or \
`*** Update File: <path>` (optionally followed by `*** Move to: <new path>`) and its hunks: each hunk \
starts with `@@`, optionally followed by a line (a class or function header) that locates it, and \
each of its lines starts with ` ` (context: three lines before and after each change), `-` (removed) \
or `+` (added). Paths are relative to the working directory. Example:\n\
*** Begin Patch\n*** Update File: src/app.py\n@@ def greet():\n-    print(\"Hi\")\n+    print(\"Hello\")\n*** End Patch";

/// The `input` parameter's description in `apply_patch`'s function form.
const APPLY_PATCH_INPUT_DESCRIPTION: &str = "The whole patch, from `*** Begin Patch` to `*** End Patch`";

static EXEC_AS_FUNCTION: AtomicBool = AtomicBool::new(false);
static APPLY_PATCH_AS_FUNCTION: AtomicBool = AtomicBool::new(false);

/// Sets, for this process, whether `exec` is offered as a function (the launcher, before Codex
/// sends anything).
pub fn set_exec_as_function(on: bool) {
    EXEC_AS_FUNCTION.store(on, Ordering::Relaxed);
}

/// Whether `exec` is offered as a function in this process.
pub fn exec_as_function() -> bool {
    EXEC_AS_FUNCTION.load(Ordering::Relaxed)
}

/// Sets, for this process, whether `apply_patch` is offered as a function (the launcher, before
/// Codex sends anything, when `DREAMFERENCE_MIGHTLING_APPLY_PATCH` is `function`).
pub fn set_apply_patch_as_function(on: bool) {
    APPLY_PATCH_AS_FUNCTION.store(on, Ordering::Relaxed);
}

/// Whether `apply_patch` is offered as a function in this process.
pub fn apply_patch_as_function() -> bool {
    APPLY_PATCH_AS_FUNCTION.load(Ordering::Relaxed)
}

/// The custom tools offered as functions in this process; empty when nothing is translated.
pub fn translated() -> Vec<&'static str> {
    let mut names = Vec::new();
    if exec_as_function() {
        names.push(EXEC);
    }
    if apply_patch_as_function() {
        names.push(APPLY_PATCH);
    }
    names
}

/// The names offered as functions so far in this process: only a call to one of these is turned
/// back into a custom tool call.
fn offered() -> &'static Mutex<HashSet<String>> {
    static OFFERED: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    OFFERED.get_or_init(Mutex::default)
}

/// Whether `tool` is a custom tool named in `names`, the tools translated ([`translated`]).
pub fn is_translated(tool: &Value, names: &[&str]) -> bool {
    tool["type"] == "custom" && tool["name"].as_str().is_some_and(|name| names.contains(&name))
}

/// A custom tool as the function ling-engine offers in its place: the one string parameter
/// [`INPUT`], and the description followed by the grammar from `format`. `apply_patch` has its
/// own description ([`APPLY_PATCH_DESCRIPTION`]).
pub fn as_function(tool: &Value) -> Value {
    let name = tool["name"].as_str().unwrap_or_default();
    let (mut description, input_description) = if name == APPLY_PATCH {
        (APPLY_PATCH_DESCRIPTION.to_string(), APPLY_PATCH_INPUT_DESCRIPTION)
    } else {
        (tool["description"].as_str().unwrap_or_default().to_string(), INPUT_DESCRIPTION)
    };
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
            "properties": {INPUT: {"type": "string", "description": input_description}},
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
/// `custom_tool_call` Codex expects. Anything else, and every item when nothing is translated,
/// comes back as it came.
pub fn from_server<T: Serialize + DeserializeOwned>(item: T) -> T {
    let names = translated();
    if names.is_empty() {
        return item;
    }
    let Ok(value) = serde_json::to_value(&item) else { return item };
    match custom_call(&value, &names) {
        Some(custom) => serde_json::from_value(custom).unwrap_or(item),
        None => item,
    }
}

/// [`from_server`] on JSON, for the tools in `names` that were offered as functions.
pub fn custom_call(item: &Value, names: &[&str]) -> Option<Value> {
    let name = item["name"].as_str()?;
    if item["type"] != "function_call" || !item["namespace"].is_null() || !names.contains(&name) {
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

/// The request's input: each earlier call to a tool in `names`, and its output, as the function
/// call and output the model saw. Items that are not changed are left untouched.
pub fn replay<T: Serialize + DeserializeOwned>(items: &mut [T], names: &[&str]) {
    let values: Vec<Value> = items.iter().map(|item| serde_json::to_value(item).unwrap_or(Value::Null)).collect();
    for (index, replayed) in replay_values(&values, names) {
        if let Ok(item) = serde_json::from_value(replayed) {
            items[index] = item;
        }
    }
}

/// [`replay`] on JSON: the index and new form of each item that changes.
pub fn replay_values(items: &[Value], names: &[&str]) -> Vec<(usize, Value)> {
    let calls: HashSet<&str> = items
        .iter()
        .filter(|item| item["type"] == "custom_tool_call" && item["namespace"].is_null())
        .filter(|item| item["name"].as_str().is_some_and(|name| names.contains(&name)))
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
        let sent = flatten_with(tools.clone(), &[EXEC]);
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
        // Off (ling-engine, or the switch's `freeform`), nothing changes.
        assert_eq!(flatten_with(tools.clone(), &[]), tools);
        // Every request sends the tools again: the result must not drift (cached prefix).
        assert_eq!(flatten_with(tools.clone(), &[EXEC]), sent);
    }

    #[test]
    fn a_function_call_to_exec_comes_back_as_the_custom_call_ling_engine_returned() {
        let recorded = recorded();
        flatten_with(list(&recorded["production_tools"]), &[EXEC]);
        let engine = &recorded["engine_output_item"];
        // What SGLang returns for the call ling-engine returned as a custom tool call.
        let arguments = json!({"input": engine["input"]}).to_string();
        let from_sglang = json!({
            "type": "function_call", "id": engine["id"], "call_id": engine["call_id"], "name": "exec",
            "arguments": arguments, "status": "completed",
        });
        let mut expected = engine.clone();
        expected.as_object_mut().map(|item| item.remove("status"));
        assert_eq!(custom_call(&from_sglang, &[EXEC]), Some(expected));
        // A call to any other tool is left alone: SGLang's recorded exec_command call.
        assert_eq!(custom_call(&recorded["production_output_item"], &[EXEC]), None);
        // A namespaced call is an MCP tool's, never exec.
        let mut namespaced = from_sglang.clone();
        namespaced["namespace"] = json!("mcp__x");
        assert_eq!(custom_call(&namespaced, &[EXEC]), None);
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
        let changed = replay_values(&input, &[EXEC]);
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
        assert!(replay_values(&again, &[EXEC]).is_empty());
    }

    #[test]
    fn an_output_with_an_image_is_kept_whole_and_other_custom_tools_are_left_alone() {
        let items = vec![
            json!({"type": "custom_tool_call", "call_id": "a", "name": "exec", "input": "image(x)"}),
            json!({"type": "custom_tool_call_output", "call_id": "a", "output": [
                {"type": "input_text", "text": "Script completed"},
                {"type": "input_image", "image_url": "data:image/png;base64,AA"},
            ]}),
            // apply_patch has its own switch: with only exec translated, its calls stay custom.
            json!({"type": "custom_tool_call", "call_id": "b", "name": "apply_patch", "input": "*** Begin Patch"}),
            json!({"type": "custom_tool_call_output", "call_id": "b", "output": "Done"}),
        ];
        let changed = replay_values(&items, &[EXEC]);
        assert_eq!(changed.len(), 2);
        assert_eq!(changed[1].1["output"], items[1]["output"]);
        // And with only apply_patch translated, exec's stay custom.
        let changed = replay_values(&items, &[APPLY_PATCH]);
        assert_eq!(changed.iter().map(|(index, _)| *index).collect::<Vec<_>>(), [2, 3]);
    }

    /// Codex's freeform `apply_patch` tool, and a patch (`fixtures/apply_patch_freeform_tool.json`).
    fn apply_patch_fixture() -> Value {
        serde_json::from_str(include_str!("../fixtures/apply_patch_freeform_tool.json")).unwrap_or(Value::Null)
    }

    #[test]
    fn apply_patch_is_offered_as_a_function_only_when_its_switch_is_on() {
        let fixture = apply_patch_fixture();
        let freeform = fixture["freeform_tool"].clone();
        let exec = json!({"type": "custom", "name": "exec", "description": "Run", "format": {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"}});
        let tools = vec![exec.clone(), freeform.clone(), json!({"type": "function", "name": "exec_command"})];
        // Only exec translated (today's SGLang default): apply_patch is sent as Codex's custom tool.
        let sent = flatten_with(tools.clone(), &[EXEC]);
        assert_eq!(sent[1], freeform);
        assert_eq!(sent[0]["type"], "function");
        // The arm's switch on: a function with the one string parameter `input`.
        let sent = flatten_with(tools.clone(), &[EXEC, APPLY_PATCH]);
        let function = &sent[1];
        assert_eq!((function["type"].as_str(), function["name"].as_str()), (Some("function"), Some("apply_patch")));
        assert_eq!(function["parameters"]["required"], json!(["input"]));
        assert_eq!(function["parameters"]["properties"]["input"]["type"], "string");
        // Its own description in place of Codex's "do not wrap the patch in JSON", then the grammar.
        let description = function["description"].as_str().unwrap_or_default();
        let grammar = freeform["format"]["definition"].as_str().unwrap_or_default();
        assert_eq!(description, format!("{APPLY_PATCH_DESCRIPTION}\n\nThe input must follow this lark grammar:\n{grammar}"));
        assert!(!description.contains("FREEFORM"));
        assert_eq!(sent[2], tools[2]);
        // Every request sends the tools again: the result must not drift (cached prefix).
        assert_eq!(flatten_with(tools.clone(), &[EXEC, APPLY_PATCH]), sent);
        // apply_patch alone (the arm on a server where exec stays custom).
        let sent = flatten_with(tools.clone(), &[APPLY_PATCH]);
        assert_eq!((sent[0].clone(), sent[1]["type"].as_str()), (exec, Some("function")));
    }

    #[test]
    fn an_apply_patch_function_call_becomes_the_custom_call_codexs_handler_runs() {
        let fixture = apply_patch_fixture();
        flatten_with(vec![fixture["freeform_tool"].clone()], &[APPLY_PATCH]);
        let patch = fixture["patch"].as_str().unwrap_or_default();
        assert!(patch.contains('\n') && patch.contains('"') && patch.contains('\t'));
        // What SGLang returns: the patch JSON-escaped in the arguments string.
        let arguments = json!({"input": patch}).to_string();
        let call = json!({"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "apply_patch", "arguments": arguments});
        let custom = custom_call(&call, &[APPLY_PATCH]);
        assert_eq!(
            custom,
            Some(json!({"type": "custom_tool_call", "id": "fc_1", "call_id": "call_1", "name": "apply_patch", "input": patch}))
        );
        // The switch off, the call is left as it came (Codex then reports an unsupported call).
        assert_eq!(custom_call(&call, &[EXEC]), None);
        assert_eq!(custom_call(&call, &[]), None);
        // Replayed on the next request as the function call and output the model saw, the patch
        // byte for byte.
        let items = vec![
            custom.unwrap_or_default(),
            json!({"type": "custom_tool_call_output", "call_id": "call_1", "output": "Success. Updated the following files:\nM src/app.py\nA notes.txt\n"}),
        ];
        let changed = replay_values(&items, &[APPLY_PATCH]);
        assert_eq!(changed.len(), 2);
        let replayed = &changed[0].1;
        assert_eq!((replayed["type"].as_str(), replayed["name"].as_str()), (Some("function_call"), Some("apply_patch")));
        assert_eq!(input_of(replayed["arguments"].as_str().unwrap_or_default()), patch);
        assert_eq!(changed[1].1, json!({"type": "function_call_output", "call_id": "call_1", "output": items[1]["output"]}));
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
        // The only test that sets the process's switches; the others pass the names explicitly.
        flatten_with(
            vec![json!({"type": "custom", "name": "exec", "description": "Run"}), apply_patch_fixture()["freeform_tool"].clone()],
            &[EXEC, APPLY_PATCH],
        );
        let call = || Item::FunctionCall { name: "exec".into(), arguments: r#"{"input":"text(1)"}"#.into(), call_id: "c".into() };
        let patch = || Item::FunctionCall {
            name: "apply_patch".into(),
            arguments: r#"{"input":"*** Begin Patch\n*** End Patch"}"#.into(),
            call_id: "p".into(),
        };
        set_exec_as_function(false);
        set_apply_patch_as_function(false);
        assert!(translated().is_empty());
        assert_eq!(from_server(call()), call());
        assert_eq!(from_server(patch()), patch());
        // Each switch turns back only its own tool's calls.
        set_apply_patch_as_function(true);
        assert_eq!(translated(), [APPLY_PATCH]);
        assert_eq!(from_server(call()), call());
        assert_eq!(
            from_server(patch()),
            Item::CustomToolCall { name: "apply_patch".into(), input: "*** Begin Patch\n*** End Patch".into(), call_id: "p".into() }
        );
        set_apply_patch_as_function(false);
        set_exec_as_function(true);
        assert_eq!(translated(), [EXEC]);
        assert_eq!(from_server(patch()), patch());
        let custom = from_server(call());
        assert_eq!(custom, Item::CustomToolCall { name: "exec".into(), input: "text(1)".into(), call_id: "c".into() });
        let mut input = vec![
            Item::Message { role: "user".into() },
            custom,
            Item::CustomToolCallOutput { call_id: "c".into(), output: json!("1") },
        ];
        replay(&mut input, &[EXEC]);
        set_exec_as_function(false);
        assert_eq!(input[0], Item::Message { role: "user".into() });
        assert_eq!(input[1], call());
        assert_eq!(input[2], Item::FunctionCallOutput { call_id: "c".into(), output: json!("1") });
    }
}
