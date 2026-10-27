// The lines each repository's working tree has changed, for the review pane's repository selector
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B32): `git diff --numstat HEAD`, staged and unstaged
// together, tracked files only (an untracked file has no lines to count against HEAD until it is
// added). A repository with no commit yet is counted against the empty tree. Local git, no network.

import { git } from "./git";

/** Lines added and removed. */
export interface Lines {
  added: number;
  removed: number;
}

/** git's empty tree: what a repository with no commit is compared with. */
const EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";

/** Sums `git diff --numstat`: `<added>\t<removed>\t<path>` per file, `-\t-` for a binary one (not counted). */
export function parseNumstat(text: string): Lines {
  const total = { added: 0, removed: 0 };
  for (const line of text.split("\n")) {
    const match = line.match(/^(\d+)\t(\d+)\t/);
    if (!match) continue;
    total.added += Number(match[1]);
    total.removed += Number(match[2]);
  }
  return total;
}

/** The lines `root` (a checked repository top level) has changed against HEAD. */
export async function changedLines(root: string): Promise<Lines> {
  let base = "HEAD";
  try {
    await git(root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"]);
  } catch {
    base = EMPTY_TREE;
  }
  return parseNumstat(await git(root, ["diff", "--numstat", "--no-color", "--no-ext-diff", "--no-renames", base, "--"]));
}
