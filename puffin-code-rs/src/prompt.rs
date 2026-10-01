//! The `# Code navigation` block the launcher appends to the model's prompt (spec §8). It lives
//! here, beside the commands it describes, so the two cannot drift apart; it rides on every turn,
//! so it is kept under 250 tokens.

use crate::paths::Repo;

/// The block when an index is ready.
pub const READY: &str = "# Code navigation

`puffin-code` answers questions about this repository's code from an index, in milliseconds:
- `puffin-code def <name>` / `refs <name>` / `callers <name>` / `callees <name>` / `impl <trait>`
- `puffin-code show <name>` prints one definition; `outline <file>`; `search <words>`
Names may be qualified (`Circle.area`, `config::load`) or given as `path:line`.
Rows are tagged `exact` (compiler index), `heuristic` (approximate) or `heuristic (text)` (a text match in a changed file); `unresolved`, `not indexed` and `not checked` lines name what the index could not see.
Before changing a signature, renaming or deleting, run `puffin-code refs`. If a row is `heuristic`, `unresolved` or `not indexed`, or there is a `not checked` line, confirm with `rg` and run the build or tests after the edit.";

/// The block while the first index is being built.
pub const BUILDING: &str = "# Code navigation

A code index for this repository is being built. Use `rg` until `puffin-code status` reports it ready; then `puffin-code refs|def|callers <name>` answer from it.";

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
    format!("\nSubmodules not indexed: {count} (`puffin-code submodules` lists them); use `rg` there.")
}

/// Which block applies to `repo`, if any.
pub fn block(repo: &Repo) -> Option<String> {
    if !repo.is_git {
        return None;
    }
    if ready(repo) {
        let settings = crate::config::Settings::load(&repo.root);
        let decisions = crate::submodules::evaluate(repo, &settings);
        return Some(format!("{READY}{}", submodules_line(&decisions)));
    }
    repo.state_dir().join("code_index.building").exists().then(|| BUILDING.to_string())
}

#[cfg(test)]
mod tests {
    #[test]
    fn blocks_stay_under_250_tokens() {
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
        assert!(long.contains("Submodules not indexed: 40 (`puffin-code submodules` lists them)"), "{long}");
        for line in [&short, &long] {
            assert!(line.len() <= super::SUBMODULES_LINE_MAX, "{line}");
        }
        let ready = super::READY.len() + super::SUBMODULES_LINE_MAX;
        assert!(ready / 4 < 250, "{ready} chars");
        assert!(super::BUILDING.len() / 4 < 250);
    }
}
