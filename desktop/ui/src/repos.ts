// Several repositories in one review pane (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B32): each
// repository's lines changed in the selector, and Last turn over all of them or one. The server writes
// a turn's paths from the repository holding the thread's folder, else from the folder itself; with
// repositories under the folder, a file belongs to the one whose top level holds it, and a file in
// none is shown under All repos only.

import type { FileDiff } from "./diff";

/** Lines added and removed. */
export interface Lines {
  added: number;
  removed: number;
}

const slashes = (value: string) => value.replace(/\\/g, "/").replace(/\/+$/, "");

/** A repository's name as the selector shows it: its folder's. */
export function repoName(repo: string): string {
  return repo.split(/[\\/]/).filter(Boolean).pop() ?? repo;
}

/** A selector option: the name, then the lines changed when they are known (`fetcher +9 −3`). */
export function repoOption(name: string, lines: Lines | null | undefined): string {
  return lines ? `${name} +${lines.added} −${lines.removed}` : name;
}

/**
 * Where Last turn's paths start: the thread's folder when the repositories lie under it (the folder is
 * in none, so the server writes paths from it), else the repository (`root`) that holds the folder.
 */
export function turnBase(cwd: string | null, repos: string[], root: string | null): string | null {
  if (cwd && repos.some((repo) => slashes(repo).startsWith(`${slashes(cwd)}/`))) return cwd;
  return root ?? cwd;
}

/** The repository of `repos` (absolute top levels) a turn's file is in, the innermost one; null for none. */
export function repoOf(file: string, cwd: string | null, repos: string[]): string | null {
  const absolute = file.startsWith("/") || /^[A-Za-z]:[\\/]/.test(file) ? slashes(file) : cwd ? `${slashes(cwd)}/${slashes(file)}` : null;
  if (!absolute) return null;
  let best: string | null = null;
  for (const repo of repos) {
    const top = slashes(repo);
    if ((absolute === top || absolute.startsWith(`${top}/`)) && (!best || top.length > slashes(best).length)) best = repo;
  }
  return best;
}

/** What the turn changed in each repository, and in all of them (`total`, files in no repository included). */
export function turnLines(files: FileDiff[], cwd: string | null, repos: string[]): { total: Lines; byRepo: Map<string, Lines> } {
  const total = { added: 0, removed: 0 };
  const byRepo = new Map<string, Lines>(repos.map((repo) => [repo, { added: 0, removed: 0 }]));
  for (const file of files) {
    total.added += file.added;
    total.removed += file.removed;
    const repo = repoOf(file.path, cwd, repos);
    const lines = repo ? byRepo.get(repo) : undefined;
    if (lines) {
      lines.added += file.added;
      lines.removed += file.removed;
    }
  }
  return { total, byRepo };
}

/** The turn's files in one repository, or all of them (`repo` null: All repos). */
export function filesIn(files: FileDiff[], cwd: string | null, repos: string[], repo: string | null): FileDiff[] {
  return repo === null ? files : files.filter((file) => repoOf(file.path, cwd, repos) === repo);
}
