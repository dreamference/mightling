// Worktree threads as the page keeps track of them (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md
// §2.6): which thread works in which managed worktree, where its work is now (the worktree or the
// local checkout, after a hand-off), the folder each turn runs in, the project it belongs to in the
// sidebar, and which worktrees the cleanup may remove. The git work is the main process's
// (desktop/electron/src/worktree.ts). Pure.

import type { WorktreeLink } from "./prefs";

/** Where a path sits relative to `from`, carried over to `to`: a worktree's folder in the local checkout and back. */
export function carry(path: string, from: string, to: string): string {
  const base = from.replace(/[\\/]+$/, "");
  if (path === base) return to;
  return path.startsWith(`${base}/`) ? `${to.replace(/[\\/]+$/, "")}/${path.slice(base.length + 1)}` : path;
}

/** The folder a thread's next turn runs in: its worktree folder, or the same folder in the local checkout after a hand-off. */
export function turnCwd(link: WorktreeLink | undefined): string | null {
  if (!link) return null;
  return link.location === "local" ? carry(link.cwd, link.checkout, link.source) : link.cwd;
}

/** The project folder a thread is listed under: a worktree thread under the folder it was started for. */
export function projectFolder(thread: { id: string; cwd: string }, links: Record<string, WorktreeLink>): string {
  return links[thread.id]?.sourceCwd ?? thread.cwd;
}

export interface ManagedEntry {
  root: string;
  owner: string | null;
  modified: number;
}

/**
 * The threads whose managed worktrees the cleanup keeps: one with a turn running, the one shown and
 * every pinned one (the vendor's rule: a worktree a pinned chat is tied to is never deleted).
 */
export function protectedOwners(running: string[], shown: string | null, pinned: string[]): Set<string> {
  return new Set([...running, ...(shown ? [shown] : []), ...pinned]);
}

/**
 * The worktrees the cleanup removes to keep the most recent `keep` (the vendor's default is 15):
 * never a permanent one, one whose thread is running or pinned, or the one just made; oldest first.
 */
export function cleanupVictims(entries: ManagedEntry[], keep: number, protectedThreads: Set<string>, permanent: string[], spare: string | null): string[] {
  const removable = entries.filter((entry) => !permanent.includes(entry.root) && entry.root !== spare && !(entry.owner && protectedThreads.has(entry.owner)));
  const excess = entries.length - Math.max(1, keep);
  if (excess <= 0) return [];
  return removable.sort((a, b) => a.modified - b.modified).slice(0, excess).map((entry) => entry.root);
}

/** The settings the worktree crate reads from `[desktop]` in config.toml, with its defaults. */
export interface WorktreeSettings {
  root: string | null;
  autoCleanup: boolean;
  keep: number;
}

export const DEFAULT_KEEP = 15;

export function worktreeSettings(desktop: Record<string, unknown> | null | undefined): WorktreeSettings {
  const root = typeof desktop?.["git-worktree-root"] === "string" && (desktop["git-worktree-root"] as string).trim() ? (desktop["git-worktree-root"] as string).trim() : null;
  const autoCleanup = typeof desktop?.["worktree-auto-cleanup-enabled"] === "boolean" ? (desktop["worktree-auto-cleanup-enabled"] as boolean) : true;
  const keep = typeof desktop?.["worktree-keep-count"] === "number" && (desktop["worktree-keep-count"] as number) > 0 ? Math.floor(desktop["worktree-keep-count"] as number) : DEFAULT_KEEP;
  return { root, autoCleanup, keep };
}
