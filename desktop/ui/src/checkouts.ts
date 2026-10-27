// Two threads on one checkout (DESKTOP_PARITY §4.5 item 28, DESKTOP §16 B9): when another thread is
// working in the folder the shown thread (or a new one) works in, the window says which, with Open and
// Hand off to a worktree; nothing is blocked. A thread's folder is its worktree's after a hand-off
// (`turnCwd`), else its own. Pure: no DOM.

import type { WorktreeLink } from "./prefs";
import type { WorkState } from "./store";

import { isWorking } from "./store";
import { turnCwd } from "./worktrees";

const trimmed = (folder: string) => folder.replace(/[\\/]+$/, "") || folder;

/** Whether two folders are one checkout's: the same folder, or one inside the other. */
export function sameCheckout(a: string, b: string): boolean {
  const x = trimmed(a);
  const y = trimmed(b);
  return x === y || x.startsWith(`${y}/`) || y.startsWith(`${x}/`) || x.startsWith(`${y}\\`) || y.startsWith(`${x}\\`);
}

/**
 * The other threads working now in `folder`'s checkout, by id: top-level threads only (a subagent
 * works for its parent), never `self`.
 */
export function busyHere(state: WorkState, links: Record<string, WorktreeLink>, folder: string | null, self: string | null): string[] {
  if (!folder) return [];
  return Object.values(state.threads)
    .filter((view) => view.thread.id !== self && !view.thread.parentThreadId && !view.thread.ephemeral && isWorking(state, view.thread.id))
    .filter((view) => sameCheckout(turnCwd(links[view.thread.id]) ?? view.thread.cwd, folder))
    .map((view) => view.thread.id);
}
