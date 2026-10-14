//! How Code Mode's `exec` reaches the model: as Codex's custom tool, or as a function.
//!
//! Codex offers `exec` as a custom (freeform) tool. ling-engine runs custom tools; SGLang and vLLM
//! drop them without a word, so a Code Mode session there never saw `exec` (measured 2026-10-10).
//! Since 2026-10-12 (the owner's decision, reversing the engine-only choice of 2026-10-10) `ling`
//! offers `exec` as a function on every server that is not ling-engine and turns the model's call
//! back into the custom tool call Codex expects (the leaf crate `ling-rs/tools`, through patch
//! 0020's hooks). The test is the one the catalog's `apply_patch` gate uses:
//! [`ServedModel::runs_custom_tools`]. `apply_patch` is not decided here; it keeps its own switch
//! ([`crate::APPLY_PATCH_ENV`], [`crate::apply_patch_as_function`]).
//!
//! [`ENV`] overrides the choice for a benchmark arm: `freeform` sends Codex's custom tool as it is
//! (the off arm of the Code Mode A/B: on SGLang the model then never sees `exec`), `function`
//! translates even on ling-engine.

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

/// Sets this process's choice, before Codex sends its first request.
pub fn configure(model: &ServedModel) {
    ling_tools::set_exec_as_function(as_function(model, std::env::var(ENV).ok().as_deref()));
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
}
