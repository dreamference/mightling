//! How Code Mode's `exec` reaches the model: as Codex's custom tool, or as a function.
//!
//! Codex offers `exec` as a custom (freeform) tool. ling-engine runs custom tools; SGLang and vLLM
//! drop them without a word, so a Code Mode session there never saw `exec` (measured 2026-10-10).
//! Since 2026-10-12 (the owner's decision, reversing the engine-only choice of 2026-10-10) `ling`
//! offers `exec` as a function on every server that is not ling-engine and turns the model's call
//! back into the custom tool call Codex expects (the leaf crate `ling-rs/tools`, through patch
//! 0020's hooks). The test is the one the catalog's `apply_patch` gate uses:
//! [`ServedModel::runs_custom_tools`]. `apply_patch` is not touched here; it keeps its own switch
//! ([`crate::APPLY_PATCH_ENV`]).
//!
//! [`ENV`] overrides the choice for a benchmark arm: `freeform` sends Codex's custom tool as it is
//! (the off arm of the Code Mode A/B: on SGLang the model then never sees `exec`), `function`
//! translates even on ling-engine.
//!
//! Where `exec` stays custom on a server that drops it (`freeform` on SGLang or vLLM), Code Mode's
//! `wait` is left out of the request too: alone, it can only fail ("exec cell … not found"), and
//! the model passed it `exec_command`'s ids instead of polling with `write_stdin`
//! (`ling_tools::custom::is_orphaned_wait`).

use crate::ServedModel;

/// The environment variable that chooses `exec`'s form: `function` or `freeform`. Unset, empty or
/// anything else leaves the automatic choice ([`as_function`]).
pub const ENV: &str = "DREAMFERENCE_MIGHTLING_EXEC_TOOL";

/// Whether `exec` is offered as a function: the override from [`ENV`] when it names a form, else
/// on every server that does not run custom tools.
pub fn as_function(model: &ServedModel, setting: Option<&str>) -> bool {
    match setting.map(|s| s.trim().to_ascii_lowercase()).as_deref() {
        Some("function") => true,
        Some("freeform") => false,
        _ => !model.runs_custom_tools(),
    }
}

/// Sets this process's choice, before Codex sends its first request: the form of `exec`, and
/// whether the server drops custom tools (which takes `wait` out when `exec` stays custom).
pub fn configure(model: &ServedModel) {
    ling_tools::set_exec_as_function(as_function(model, std::env::var(ENV).ok().as_deref()));
    ling_tools::set_custom_tools_dropped(!model.runs_custom_tools());
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::LING_ENGINE_OWNER;

    fn served(owner: &str) -> ServedModel {
        ServedModel { id: "m".into(), max_model_len: 65_536, owned_by: owner.into() }
    }

    #[test]
    fn exec_is_a_function_everywhere_but_on_ling_engine() {
        for owner in ["sglang", "vllm", "", "library"] {
            assert!(as_function(&served(owner), None), "{owner}");
            assert!(as_function(&served(owner), Some("")), "{owner}");
        }
        assert!(!as_function(&served(LING_ENGINE_OWNER), None));
    }

    #[test]
    fn the_switch_names_the_form_for_a_benchmark_arm() {
        // The A/B's off arm: Codex's custom tool as it is, which SGLang drops.
        assert!(!as_function(&served("sglang"), Some("freeform")));
        assert!(!as_function(&served("sglang"), Some(" Freeform ")));
        assert!(as_function(&served(LING_ENGINE_OWNER), Some("function")));
        // A word the switch does not know leaves the automatic choice.
        assert!(as_function(&served("sglang"), Some("off-ish")));
        assert!(!as_function(&served(LING_ENGINE_OWNER), Some("yes")));
    }

    #[test]
    fn wait_reaches_the_model_only_beside_an_exec_it_can_see() {
        // Codex's two Code Mode tools, as every request carries them.
        let tools = vec![
            serde_json::json!({"type": "custom", "name": "exec", "description": "Run"}),
            serde_json::json!({"type": "function", "name": "wait", "description": "Waits on a yielded `exec` cell"}),
        ];
        let sent = |owner: &str, setting: Option<&str>| -> Vec<String> {
            let model = served(owner);
            ling_tools::flatten_with(tools.clone(), as_function(&model, setting), !model.runs_custom_tools())
                .iter()
                .map(|tool| format!("{} {}", tool["type"].as_str().unwrap_or_default(), tool["name"].as_str().unwrap_or_default()))
                .collect()
        };
        // SGLang, automatic: `exec` as a function, and `wait` for its cells.
        assert_eq!(sent("sglang", None), ["function exec", "function wait"]);
        // SGLang with `freeform` (the Code Mode A/B's off arm): the server drops `exec`, so `wait`
        // is not offered alone.
        assert_eq!(sent("sglang", Some("freeform")), ["custom exec"]);
        assert_eq!(sent("vllm", Some("freeform")), ["custom exec"]);
        // ling-engine runs custom tools: both as Codex sends them, or both as functions.
        assert_eq!(sent(LING_ENGINE_OWNER, None), ["custom exec", "function wait"]);
        assert_eq!(sent(LING_ENGINE_OWNER, Some("function")), ["function exec", "function wait"]);
    }
}
