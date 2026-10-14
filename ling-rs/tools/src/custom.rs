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
//! Two things reach only the function form, after SWE-bench night 6 (2026-10-14; the analysis in
//! `docs/dev/code-mode-night6.md`: 7.7% of cells failed, mostly JavaScript syntax errors from
//! Python or shell heredocs pasted into a string, and `exec_command` called without the `tools.`
//! prefix; 92% of the cells wrapped one shell command):
//!
//! - [`GUIDANCE`], appended to `exec`'s description before the grammar: when to call
//!   `exec_command` directly, the `tools.` prefix, what a cell gets back, no heredocs in a string;
//! - a [`hint`] appended to a failed cell's output when the error is one of the known kinds, so
//!   the model learns from one failure instead of a burst of them. The hint is a pure function of
//!   the cell and its output, so the cached prefix holds.
//!
//! Both are on unless [`FIXES_ENV`] says `off` (a benchmark arm that reproduces night 6's form).

use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::Mutex;
use std::sync::OnceLock;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::AtomicU8;
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

/// The environment variable that switches the night-6 fixes off for a benchmark arm: `off` sends
/// `exec`'s description and a failed cell's output as night 6 saw them (and tells the ledger hook,
/// which reads the same variable, to leave its two Code Mode lines out). Unset, empty or anything
/// else leaves them on.
pub const FIXES_ENV: &str = "DREAMFERENCE_MIGHTLING_CODE_MODE_FIXES";

/// 0: not read yet; 1: on; 2: off.
static FIXES: AtomicU8 = AtomicU8::new(0);

/// Whether the guidance and the hints are on in this process: [`FIXES_ENV`], read once.
pub fn fixes_on() -> bool {
    match FIXES.load(Ordering::Relaxed) {
        0 => {
            let on = fixes_on_from(std::env::var(FIXES_ENV).ok().as_deref());
            FIXES.store(if on { 1 } else { 2 }, Ordering::Relaxed);
            on
        }
        state => state == 1,
    }
}

/// [`fixes_on`] from the variable's value.
pub fn fixes_on_from(setting: Option<&str>) -> bool {
    !setting.is_some_and(|value| value.trim().eq_ignore_ascii_case("off"))
}

/// What the function form adds to `exec`'s description, before the grammar. Each line answers one
/// kind of failure night 6 counted, or the wrapper cells that made up 92% of the calls.
pub const GUIDANCE: &str = "How to use `exec` here:
- For one shell command, call `exec_command` directly. Use `exec` only to chain several tool calls or to work on their output in JavaScript.
- Inside a cell the nested tools are on `tools`: `const r = await tools.exec_command({cmd: \"...\"}); text(r.output);`. A bare `exec_command(...)` is not defined. `exec_command` returns `{output, exit_code, ...}`: print `r.output` and check `r.exit_code`.
- Tools offered to you directly (`code_show`, `code_search`, ...) are not reachable from a cell: call them directly. `exec` cannot call itself.
- The input is JavaScript only: no Python statements, and no shell heredoc (`<<'EOF'`) inside a JavaScript string, where its quotes break the cell. Run a script by calling `exec_command` directly with `python - <<'EOF' ... EOF`, or write it to a file first.
- Each cell is a fresh isolate: a variable from an earlier cell is gone (`store`/`load` carry values across cells).";

/// What is put between a failed cell's output and its hint.
const HINT_PREFIX: &str = "\n\nHint: ";

/// The hint for a failed cell, from its input and output text, or `None` when the output is not a
/// failure of a known kind. Policy refusals (`exec_command failed: … rejected`) and failures the
/// model could not have written differently get none.
pub fn hint(input: &str, output: &str) -> Option<&'static str> {
    if !output.starts_with("Script failed") || output.contains(HINT_PREFIX) {
        return None;
    }
    let error = output.split("Script error:\n").nth(1)?.lines().next()?.trim();
    if let Some(rest) = error.strip_prefix("ReferenceError: ")
        && let Some(name) = rest.strip_suffix(" is not defined")
    {
        return Some(if name == "exec_command" || name == "write_stdin" || name == "exec" || name == "tools" {
            "nested tools are on `tools`: `const r = await tools.exec_command({cmd: \"...\"}); text(r.output);`. For one shell command, call `exec_command` directly instead of wrapping it in a cell."
        } else {
            "each cell is a fresh isolate: a variable from an earlier cell is gone, so declare it in this cell (`store`/`load` carry values across cells). A failed line also defines nothing."
        });
    }
    if let Some(rest) = error.strip_prefix("TypeError: tools.")
        && let Some(name) = rest.strip_suffix(" is not a function")
    {
        return Some(if name == "exec" {
            "`exec` cannot call itself: put the JavaScript in the cell, or call `exec_command` directly."
        } else {
            "that tool is not reachable from a cell: call it directly, as its own tool call, if it is offered to you. Inside a cell use `tools.exec_command`."
        });
    }
    if error.starts_with("SyntaxError") {
        let python = input.lines().map(str::trim).filter(|line| !line.is_empty()).take(3).any(|line| {
            line.starts_with("import ") || line.starts_with("from ") || line.starts_with("print(") || line.starts_with("def ")
        });
        return Some(if input.contains("<<") || python {
            "the cell's input is JavaScript only. A shell heredoc or Python inside a JavaScript string breaks the cell's quoting: call `exec_command` directly with the command (`python - <<'EOF' ... EOF` is fine there), or write the script to a file first. One command per call is fine."
        } else {
            "the cell's input is JavaScript only, with no quoting around it. For one shell command, call `exec_command` directly instead; use a cell only to chain tool calls or process their output."
        });
    }
    None
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
/// [`INPUT`], and the description followed by the grammar from `format`, with [`GUIDANCE`]
/// between them unless the fixes are off.
pub fn as_function(tool: &Value) -> Value {
    as_function_with(tool, fixes_on())
}

/// [`as_function`], with the guidance when `fixes` is true.
pub fn as_function_with(tool: &Value, fixes: bool) -> Value {
    let name = tool["name"].as_str().unwrap_or_default();
    let mut description = tool["description"].as_str().unwrap_or_default().to_string();
    if fixes {
        if !description.is_empty() {
            description.push_str("\n\n");
        }
        description.push_str(GUIDANCE);
    }
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

/// [`replay`] on JSON: the index and new form of each item that changes. A failed cell's output
/// gets its [`hint`] unless the fixes are off.
pub fn replay_values(items: &[Value]) -> Vec<(usize, Value)> {
    replay_values_with(items, fixes_on())
}

/// [`replay_values`], with the hints when `fixes` is true.
pub fn replay_values_with(items: &[Value], fixes: bool) -> Vec<(usize, Value)> {
    let calls: HashMap<&str, &str> = items
        .iter()
        .filter(|item| item["type"] == "custom_tool_call" && item["namespace"].is_null())
        .filter(|item| item["name"].as_str().is_some_and(|name| TRANSLATED.contains(&name)))
        .filter_map(|item| Some((item["call_id"].as_str()?, item["input"].as_str().unwrap_or_default())))
        .collect();
    let mut changed = Vec::new();
    for (index, item) in items.iter().enumerate() {
        let Some((call_id, input)) = item["call_id"].as_str().and_then(|id| calls.get_key_value(id)) else { continue };
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
            let mut output = output_text(&item["output"]);
            if fixes
                && let Value::String(text) = &mut output
                && let Some(hint) = hint(input, text)
            {
                text.push_str(HINT_PREFIX);
                text.push_str(hint);
            }
            json!({"type": "function_call_output", "call_id": call_id, "output": output})
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
        let sent = flatten_with(tools.clone(), true);
        // The custom tool is now a function with one string parameter; the description is Codex's,
        // then the night-6 guidance, then the grammar worded as ling-engine words it (its API
        // spec §4). The tests run with the variable unset, so the fixes are on.
        let function = &sent[0];
        assert_eq!(function["type"], "function");
        assert_eq!(function["name"], "exec");
        let description = function["description"].as_str().unwrap_or_default();
        let night6 = format!(
            "{}\n\nThe input must follow this lark grammar:\n{}",
            exec["description"].as_str().unwrap_or_default(),
            exec["format"]["definition"].as_str().unwrap_or_default()
        );
        let expected = format!(
            "{}\n\n{GUIDANCE}\n\nThe input must follow this lark grammar:\n{}",
            exec["description"].as_str().unwrap_or_default(),
            exec["format"]["definition"].as_str().unwrap_or_default()
        );
        assert_eq!(description, expected);
        assert!(fixes_on(), "the tests run with {FIXES_ENV} unset");
        // The off arm sends what night 6 sent.
        assert_eq!(as_function_with(exec, false)["description"], night6);
        assert_eq!(
            function["parameters"],
            json!({"type": "object", "properties": {"input": {"type": "string", "description": "The tool's input, as free text"}}, "required": ["input"]})
        );
        // The other tools are sent as they came.
        assert_eq!(sent[1..], tools[1..]);
        // Off (ling-engine, or the switch's `freeform`), nothing changes.
        assert_eq!(flatten_with(tools.clone(), false), tools);
        // Every request sends the tools again: the result must not drift (cached prefix).
        assert_eq!(flatten_with(tools.clone(), true), sent);
    }

    #[test]
    fn a_function_call_to_exec_comes_back_as_the_custom_call_ling_engine_returned() {
        let recorded = recorded();
        flatten_with(list(&recorded["production_tools"]), true);
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

    /// Failed cells as night 6 recorded them (`docs/dev/code-mode-night6.md`, "Failed cells").
    const FAILED: &str = "Script failed\nWall time 0.0 seconds\nOutput:\nScript error:\n";

    #[test]
    fn a_failed_cell_of_a_known_kind_gets_a_hint_and_the_rest_do_not() {
        let failed = |error: &str| format!("{FAILED}{error}");
        // The call without the `tools.` prefix: 36 cells.
        let prefix = hint("const r = await exec_command({cmd: 'ls'});", &failed("ReferenceError: exec_command is not defined"));
        assert!(prefix.is_some_and(|hint| hint.contains("tools.exec_command")), "{prefix:?}");
        assert_eq!(hint("exec('ls')", &failed("ReferenceError: exec is not defined")), prefix);
        // A variable from an earlier cell, or from a line that failed.
        let variable = hint("text(r.output)", &failed("ReferenceError: r is not defined"));
        assert!(variable.is_some_and(|hint| hint.contains("fresh isolate")), "{variable:?}");
        // Tools that are not in the isolate: 28 cells.
        let itself = hint("await tools.exec('text(1)')", &failed("TypeError: tools.exec is not a function\n    at exec_main.mjs:1:13"));
        assert!(itself.is_some_and(|hint| hint.contains("cannot call itself")), "{itself:?}");
        let direct = hint("await tools.code_show({name: 'x'})", &failed("TypeError: tools.code_show is not a function"));
        assert!(direct.is_some_and(|hint| hint.contains("call it directly")), "{direct:?}");
        assert_eq!(hint("await tools.apply_patch(`x`)", &failed("TypeError: tools.apply_patch is not a function")), direct);
        // Python or a heredoc in a cell: ~115 syntax errors.
        let python = hint("import subprocess\nout = subprocess.run(['ls'])\n", &failed("SyntaxError: Unexpected identifier 'out'"));
        assert!(python.is_some_and(|hint| hint.contains("heredoc") && hint.contains("`exec_command` directly")), "{python:?}");
        let heredoc = hint("const r = await tools.exec_command({cmd: `python - <<'EOF'\nprint('x')\nEOF`});", &failed("SyntaxError: Unexpected token ')'"));
        assert_eq!(heredoc, python);
        let quoting = hint("await tools.exec_command({cmd: \"grep -rn \\\"x\\\" .\"});", &failed("SyntaxError: missing ) after argument list"));
        assert!(quoting.is_some_and(|hint| hint.contains("JavaScript only")) && quoting != python, "{quoting:?}");
        // A policy refusal, a cell that worked, and a hinted output already, get nothing.
        assert_eq!(hint("await tools.exec_command({cmd: 'rm -rf x'})", &failed("exec_command failed: CreateProcess { message: \"rejected: rm -f style commands are not permitted\" }")), None);
        assert_eq!(hint("text(1)", "Script completed\nWall time 0.0 seconds\nOutput:\n1"), None);
        assert_eq!(hint("text(1)", "Script running with cell ID 3\nWall time 30.0 seconds\nOutput:\n"), None);
        let hinted = format!("{}{HINT_PREFIX}{}", failed("ReferenceError: exec is not defined"), prefix.unwrap_or_default());
        assert_eq!(hint("exec('ls')", &hinted), None);
    }

    #[test]
    fn the_replayed_output_of_a_failed_cell_carries_the_hint_unless_the_fixes_are_off() {
        let items = vec![
            json!({"type": "custom_tool_call", "call_id": "a", "name": "exec", "input": "const r = await exec_command({cmd: 'ls'});"}),
            json!({"type": "custom_tool_call_output", "call_id": "a", "output": [
                {"type": "input_text", "text": "Script failed\nWall time 0.0 seconds\nOutput:\n"},
                {"type": "input_text", "text": "Script error:\nReferenceError: exec_command is not defined"},
            ]}),
            json!({"type": "custom_tool_call", "call_id": "b", "name": "exec", "input": "text(1)"}),
            json!({"type": "custom_tool_call_output", "call_id": "b", "output": "Script completed\nWall time 0.0 seconds\nOutput:\n1"}),
        ];
        let on = replay_values_with(&items, true);
        let output = on[1].1["output"].as_str().unwrap_or_default();
        assert!(output.starts_with("Script failed\nWall time 0.0 seconds\nOutput:\nScript error:\nReferenceError: exec_command is not defined\n\nHint: nested tools are on `tools`"), "{output}");
        assert_eq!(on[3].1["output"], items[3]["output"]);
        // The same request every time: a hinted history gets no second hint.
        let mut again = items.clone();
        for (index, item) in on.clone() {
            again[index] = item;
        }
        assert!(replay_values_with(&again, true).is_empty());
        // The off arm: the output as night 6 saw it.
        let off = replay_values_with(&items, false);
        assert_eq!(off[1].1["output"], "Script failed\nWall time 0.0 seconds\nOutput:\nScript error:\nReferenceError: exec_command is not defined");
        // The variable's spellings.
        assert!(fixes_on_from(None) && fixes_on_from(Some("")) && fixes_on_from(Some("on")));
        assert!(!fixes_on_from(Some("off")) && !fixes_on_from(Some(" OFF ")));
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
        flatten_with(vec![json!({"type": "custom", "name": "exec", "description": "Run"})], true);
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
