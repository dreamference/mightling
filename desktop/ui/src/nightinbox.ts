// Night Shift's results in Scheduled's inbox (DESKTOP §16 B41, DESKTOP_PARITY §9.5.7). Each finished
// run is unread until it is opened, or until Mark all as read: the marks are two window preferences,
// `nightSeen` (when everything was last marked read, which before B41 opening Scheduled did) and
// `nightRead` (the runs opened since). The sidebar's Scheduled counts the unread runs, and the main
// process counts them for the tray from the same preferences (desktop/electron/src/nightinbox.ts,
// whose rule this mirrors). Tasks `ling-admin night archive` moved away leave the window's marks and
// thread ties (`nightThreads`, B40) behind; `prunedNight` drops them. Pure.

import type { NightTask } from "./bridge";
import { cleanNightThreads, type NightThreads } from "./nightthread";

/** Statuses of a task that has run, as the inbox lists them (ScheduledView.tsx `FINISHED`). */
export const INBOX_STATUSES = ["done", "no-change", "stalled", "failed", "cancelled", "blocked"];

/** At most this many opened runs are remembered (the preference holds 500 strings at most). */
export const NIGHT_READ_MAX = 400;

/** A task gone from the queue is forgotten only when it was queued more than this long ago: never one just added. */
export const FORGET_AFTER_MS = 24 * 60 * 60 * 1000;

type Run = Pick<NightTask, "id" | "status" | "at">;

/** Whether a finished run is unread: finished after the last Mark all as read, and not opened since. */
export function isUnread(task: Run, seen: string | null, read: readonly string[]): boolean {
  if (!INBOX_STATUSES.includes(task.status)) return false;
  if (read.includes(task.id)) return false;
  return !seen || finishedAfter(task.at, seen);
}

/**
 * Whether `at` is later than `seen`, as times: Night Shift writes local time with its offset
 * (night_shift_queue.py `now`), Mark all as read UTC (`toISOString`), so their text does not sort.
 */
function finishedAfter(at: string | null, seen: string): boolean {
  const finished = Date.parse(at ?? "");
  const marked = Date.parse(seen);
  return Number.isNaN(finished) || Number.isNaN(marked) ? (at ?? "") > seen : finished > marked;
}

/** How many finished runs are unread. */
export function unreadRuns(tasks: Run[], seen: string | null, read: readonly string[]): number {
  return tasks.filter((task) => isUnread(task, seen, read)).length;
}

/** The opened runs with `id` added (the oldest dropped beyond `NIGHT_READ_MAX`), or the same list when it is there. */
export function markRead(read: readonly string[], id: string): string[] {
  if (read.includes(id)) return [...read];
  return [...read, id].slice(-NIGHT_READ_MAX);
}

/** When a task was queued, from its id (`<yyyymmdd>-<hhmm>-<3 hex>`, the launcher's), in milliseconds; null for another id. */
export function queuedAt(id: string): number | null {
  const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(?:-|$)/.exec(id);
  if (!match) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  return new Date(year, month - 1, day, hour, minute).getTime();
}

/**
 * The preference changes that forget tasks no longer in the queue (archived, or removed by hand):
 * their read marks and their thread ties. A task missing for less than a day is kept, so a task
 * `ling night add` has just written is never forgotten before the list shows it. Null when nothing
 * changes.
 */
export function prunedNight(prefs: { nightRead: readonly string[]; nightThreads: NightThreads }, live: readonly string[], now: number): { nightRead?: string[]; nightThreads?: NightThreads } | null {
  const present = new Set(live);
  const gone = (id: string) => {
    if (present.has(id)) return false;
    const at = queuedAt(id);
    return at === null || now - at > FORGET_AFTER_MS;
  };
  const changes: { nightRead?: string[]; nightThreads?: NightThreads } = {};
  const read = (Array.isArray(prefs.nightRead) ? prefs.nightRead : []).filter((id) => !gone(id));
  if (read.length !== (prefs.nightRead?.length ?? 0)) changes.nightRead = read;
  const ties = cleanNightThreads(prefs.nightThreads);
  const kept = Object.fromEntries(Object.entries(ties).filter(([id]) => !gone(id)));
  if (Object.keys(kept).length !== Object.keys(prefs.nightThreads ?? {}).length) changes.nightThreads = kept;
  return Object.keys(changes).length ? changes : null;
}
