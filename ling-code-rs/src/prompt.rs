//! The `# Code navigation` block the launcher appends to the model's prompt (spec §8). It lives
//! here, beside the commands it describes, so the two cannot drift apart; it rides on every turn,
//! so it is kept under 300 tokens.
//!
//! **The block says when to use the index, not only what it offers** (spec §15, measured
//! 2026-10-02). The first wording listed the commands and had one instruction, "before changing a
//! signature, renaming or deleting, run `ling-code refs`": on twelve navigation tasks the model
//! queried the index in six, and in none of the ones phrased as a bug to fix, where it does not
//! know a name yet and nothing told it that `search` is where to start.

use crate::paths::Repo;

/// The block when an index is ready and the model reaches it through the shell.
pub const READY: &str = "# Code navigation

This repository has a code index, `ling-code`. Make it your first step whenever you need to find code, before `rg`, `grep`, `find` or reading files:
- You do not know the name (a bug report, a feature, \"the code that does X\"): `ling-code search <words>` finds the definitions; then `show <name>`, or `outline <file>` before reading a file you have not seen.
- You know a name: `ling-code def <name>` (where it is), `show <name>` (its source), `refs <name>` (every use), `callers <name>` / `callees <name>`, `impl <trait>`.
- Before you change a definition: `ling-code impact <name>` (or `--diff`) says what breaks.
Names may be qualified (`Circle.area`, `config::load`) or given as `path:line`.
`grep` is for text that is not code: strings, comments, config keys, docs. Rows are tagged `exact`, `heuristic` or `heuristic (text)`; confirm with `grep` when a row is `heuristic`, `unresolved` or `not indexed`, or a `not checked` line appears, and run the build or tests after an edit.";

/// The block when the launcher also offers the index as tools (`prompt-block --tools`): the
/// same operations under the names the model calls.
pub const READY_TOOLS: &str = "# Code navigation

This repository has a code index, behind the `code_*` tools (the same answers as `ling-code <verb>` in a shell). Make it your first step whenever you need to find code, before `rg`, `grep`, `find` or reading files:
- You do not know the name (a bug report, a feature, \"the code that does X\"): `code_search` with a few of its words finds the definitions.
- You know a name: `code_def` (where it is), `code_show` (its source), `code_refs` (every use), `code_callers` / `code_callees`, `code_impl`.
- Before reading a file you have not seen, `code_outline` it and `code_show` the definition you need, instead of `cat`; `code_impact` says what breaks before you change one.
Names may be qualified (`Circle.area`, `config::load`) or given as `path:line`.
`grep` is for text that is not code: strings, comments, config keys, docs. Rows are tagged `exact`, `heuristic` or `heuristic (text)`; confirm with `grep` when a row is `heuristic`, `unresolved` or `not indexed`, or a `not checked` line appears, and run the build or tests after an edit.";

/// The block while the first index is being built.
pub const BUILDING: &str = "# Code navigation

A code index for this repository is being built. Use `rg` until `ling-code status` reports it ready; then `ling-code refs|def|callers <name>` answer from it.";

/// Whether an index exists for `repo`.
pub fn ready(repo: &Repo) -> bool {
    if !repo.is_git {
        return false;
    }
    let (graph, _) = crate::graph::locate(repo);
    let manifest = crate::manifest::Manifest::load(&repo.scip_dir());
    graph.is_file() || manifest.runs.values().any(|r| !r.store.is_empty())
}

/// Characters the submodules line may add to the block, which rides on every turn.
const SUBMODULES_LINE_MAX: usize = 110;

/// The line the ready block ends with when submodules are left out (spec §4.3): what every
/// answer says, with what to do about it. Names that would not fit the block's budget give way
/// to their count.
pub fn submodules_line(decisions: &[crate::submodules::Submodule]) -> String {
    let Some(names) = crate::submodules::not_indexed_line(decisions) else { return String::new() };
    let line = format!("\nSubmodules not indexed: {names}; use `rg` there.");
    if line.len() <= SUBMODULES_LINE_MAX {
        return line;
    }
    let count = decisions.iter().filter(|s| !s.indexed).count();
    format!("\nSubmodules not indexed: {count} (`ling-code submodules` lists them); use `rg` there.")
}

/// Which block applies to `repo`, if any; with `tools`, the one that names the `code_*` tools.
pub fn block(repo: &Repo, tools: bool) -> Option<String> {
    if !repo.is_git {
        return None;
    }
    if ready(repo) {
        let settings = crate::config::Settings::load(&repo.root);
        let decisions = crate::submodules::evaluate(repo, &settings);
        return Some(format!("{}{}", if tools { READY_TOOLS } else { READY }, submodules_line(&decisions)));
    }
    repo.state_dir().join("code_index.building").exists().then(|| BUILDING.to_string())
}

#[cfg(test)]
mod tests {
    #[test]
    fn blocks_stay_under_300_tokens() {
        // About four characters a token for English prose and code names.
        // The submodules line at its longest: names that fit, and the count that replaces names
        // that would not.
        let left_out = |path: &str| crate::submodules::Submodule {
            path: path.into(),
            name: path.into(),
            indexed: false,
            reason: crate::submodules::Reason::OtherOrganisation,
            namespace: crate::submodules::Namespace::Different,
            our_namespace: None,
            their_namespace: None,
            commits: 0,
            ours: 0,
            files: 0,
            shallow: false,
            tag: None,
            inherits_trust: false,
        };
        let short = super::submodules_line(&[left_out("codex"), left_out("vendor/x")]);
        assert!(short.contains("codex/ (other organisation), vendor/x/ (other organisation)"), "{short}");
        let many: Vec<_> = (0..40).map(|n| left_out(&format!("vendor/third_party/some-long-library-name-{n}"))).collect();
        let long = super::submodules_line(&many);
        assert!(long.contains("Submodules not indexed: 40 (`ling-code submodules` lists them)"), "{long}");
        for line in [&short, &long] {
            assert!(line.len() <= super::SUBMODULES_LINE_MAX, "{line}");
        }
        for block in [super::READY, super::READY_TOOLS] {
            let ready = block.len() + super::SUBMODULES_LINE_MAX;
            assert!(ready / 4 < 300, "{ready} chars");
            // Each block starts from when to use the index, and the launcher tells the two apart
            // by these words (ling-rs/src/code_index.rs, `named_in`).
            assert!(block.contains("Make it your first step whenever you need to find code"), "{block}");
        }
        assert!(super::READY_TOOLS.contains("`code_*` tools") && !super::READY.contains("`code_*` tools"));
        assert!(super::BUILDING.contains("is being built"));
        assert!(super::BUILDING.len() / 4 < 250);
    }
}
