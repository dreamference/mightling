// Several threads chosen at once in the sidebar, and deleting threads for good (DESKTOP §16 B36,
// DESKTOP_PARITY_CLAUDE §2.1). Ctrl-click (Cmd-click on a Mac) adds a thread to the choice or takes
// it out, Shift-click chooses every row from the last one clicked to this one, in the order the
// sidebar shows them; a plain click opens a thread as before and clears the choice. The choice's bar
// archives, moves to a project or deletes them all. `thread/delete` removes a thread and the
// subagents' threads it started from the server's store; the window then forgets what it kept
// about them (`forgetThreads`). Pure.

import type { Prefs } from "./prefs";

/** The threads chosen, and the row the next Shift-click counts from. */
export interface Choice {
  ids: string[];
  anchor: string | null;
}

export const NO_CHOICE: Choice = { ids: [], anchor: null };

/** The modifier keys of a click on a thread row. */
export interface ClickKeys {
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

/**
 * The rows from `anchor` to the row at `target` (an index into `order`, the sidebar's rows top to
 * bottom; a thread listed twice, under Pinned and in its project, is counted where it is nearest),
 * each thread once, in the order shown.
 */
export function rowRange(order: string[], anchor: string, target: number): string[] {
  const positions = order.map((id, index) => (id === anchor ? index : -1)).filter((index) => index >= 0);
  if (positions.length === 0 || target < 0 || target >= order.length) return target >= 0 && target < order.length ? [order[target]] : [];
  const from = positions.reduce((best, index) => (Math.abs(index - target) < Math.abs(best - target) ? index : best), positions[0]);
  const [low, high] = from <= target ? [from, target] : [target, from];
  return [...new Set(order.slice(low, high + 1))];
}

/**
 * The choice after a click on the row at `target` in `order`, or null for a plain click (which opens
 * the thread). Ctrl or Cmd toggles the row; Shift chooses the range from the anchor (else the thread
 * shown, else the row itself), and with Ctrl or Cmd too adds the range to what was chosen.
 */
export function clicked(choice: Choice, order: string[], target: number, keys: ClickKeys, shown: string | null): Choice | null {
  const id = order[target];
  if (id === undefined) return null;
  const toggle = keys.ctrlKey || keys.metaKey;
  if (keys.shiftKey) {
    const anchor = choice.anchor ?? shown ?? id;
    const range = rowRange(order, anchor, target);
    return { ids: toggle ? [...new Set([...choice.ids, ...range])] : range, anchor };
  }
  if (!toggle) return null;
  const ids = choice.ids.includes(id) ? choice.ids.filter((entry) => entry !== id) : [...choice.ids, id];
  return { ids, anchor: id };
}

/** The choice without the threads that left the window (archived, deleted, or never loaded). */
export function present(choice: Choice, known: (id: string) => boolean): Choice {
  const ids = choice.ids.filter(known);
  return ids.length === choice.ids.length ? choice : { ids, anchor: choice.anchor && known(choice.anchor) ? choice.anchor : null };
}

/** At most this many names are listed in the confirmation; the rest are counted. */
export const NAMES_LISTED = 12;

/**
 * The confirmation before deleting: how many threads, each by name, that it cannot be undone, and
 * what goes with them and what stays.
 */
export function deleteQuestion(labels: string[], askFolders: boolean): string {
  const count = labels.length;
  const what = count === 1 ? "this thread" : `these ${count} threads`;
  const names = labels.slice(0, NAMES_LISTED).map((label) => `• ${label.length > 80 ? `${label.slice(0, 79)}…` : label}`);
  if (count > NAMES_LISTED) names.push(`…and ${count - NAMES_LISTED} more`);
  const kept = askFolders ? " Files the agent wrote in a question's folder stay on disk." : "";
  return `Delete ${what} for good? ${count === 1 ? "It" : "They"} cannot be restored, and Ctrl+Z will not bring ${count === 1 ? "it" : "them"} back. Subagents' threads started from ${count === 1 ? "it" : "them"} go too.${kept}\n\n${names.join("\n")}`;
}

/** The preferences that name a thread, as `forgetThreads` reads and rewrites them. */
type ThreadPrefs = Pick<Prefs, "pinnedThreads" | "unreadThreads" | "sections" | "messageVersions" | "nightThreads" | "threadSummaries" | "projectContextSent" | "worktreeThreads">;

const without = <T>(record: Record<string, T>, gone: Set<string>) => Object.fromEntries(Object.entries(record).filter(([id]) => !gone.has(id)));

/**
 * The window's preferences with the deleted threads forgotten: pins, unread marks, sections, the
 * versions of edited messages (an entry whose first version went, or left with one, is dropped),
 * Night Shift tasks tied to them, summaries, the project context stamps and worktree links. Only the
 * keys that change are returned, so nothing else is written.
 */
export function forgetThreads(prefs: ThreadPrefs, ids: string[]): Partial<ThreadPrefs> {
  const gone = new Set(ids);
  const changed: Partial<ThreadPrefs> = {};
  if (prefs.pinnedThreads.some((id) => gone.has(id))) changed.pinnedThreads = prefs.pinnedThreads.filter((id) => !gone.has(id));
  if (prefs.unreadThreads.some((id) => gone.has(id))) changed.unreadThreads = prefs.unreadThreads.filter((id) => !gone.has(id));
  if (prefs.sections.some((section) => section.threads.some((id) => gone.has(id)))) {
    changed.sections = prefs.sections.map((section) => ({ ...section, threads: section.threads.filter((id) => !gone.has(id)) }));
  }
  const versions: Prefs["messageVersions"] = {};
  let versionsChanged = false;
  for (const [key, threads] of Object.entries(prefs.messageVersions)) {
    const kept = threads.filter((id) => !gone.has(id));
    if (kept.length !== threads.length) versionsChanged = true;
    if (kept.length >= 2 && kept[0] === threads[0]) versions[key] = kept;
    else if (kept.length === threads.length) versions[key] = threads;
  }
  if (versionsChanged) changed.messageVersions = versions;
  const tasks = Object.fromEntries(Object.entries(prefs.nightThreads).filter(([, thread]) => !gone.has(thread)));
  if (Object.keys(tasks).length !== Object.keys(prefs.nightThreads).length) changed.nightThreads = tasks;
  for (const key of ["threadSummaries", "projectContextSent", "worktreeThreads"] as const) {
    if (Object.keys(prefs[key]).some((id) => gone.has(id))) (changed as Record<string, unknown>)[key] = without(prefs[key] as Record<string, unknown>, gone);
  }
  return changed;
}
