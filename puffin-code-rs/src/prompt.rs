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
Rows are tagged `exact` (compiler index), `heuristic` (approximate) or `heuristic (text)` (a text match in a file changed since the index). `unresolved`, `not indexed` and `not checked` lines name what the index could not see.
Before changing a signature, renaming or deleting, run `puffin-code refs`. If any row is `heuristic`, `unresolved` or `not indexed`, or the answer has a `not checked` line, confirm with `rg` and run the build or tests after the edit.";

/// The block while the first index is being built.
pub const BUILDING: &str = "# Code navigation

A code index for this repository is being built. Use `rg` until `puffin-code status` reports it ready; then `puffin-code refs|def|callers <name>` answer from it.";

/// Which block applies to `repo`, if any.
pub fn block(repo: &Repo) -> Option<&'static str> {
    if !repo.is_git {
        return None;
    }
    let (graph, _) = crate::graph::locate(repo);
    let manifest = crate::manifest::Manifest::load(&repo.scip_dir());
    let ready = graph.is_file() || manifest.runs.values().any(|r| !r.store.is_empty());
    if ready {
        return Some(READY);
    }
    repo.state_dir().join("code_index.building").exists().then_some(BUILDING)
}

#[cfg(test)]
mod tests {
    #[test]
    fn blocks_stay_under_250_tokens() {
        // About four characters a token for English prose and code names.
        assert!(super::READY.len() / 4 < 250, "{} chars", super::READY.len());
        assert!(super::BUILDING.len() / 4 < 250);
    }
}
