// Night Shift tasks inside a Work thread (DESKTOP §16 B40, DESKTOP_PARITY §9.5.7). A task queued from
// a thread (`/night add` there, or Scheduled's Queue with "With what was said in …" ticked) shows in
// that thread as a card: queued, running, then done with its attempts, the chosen branch and its
// block from the morning report, as a note. The note is the window's: it is never an item of the
// thread and never reaches the model (tonight.ts reads only the thread's messages).
//
// The tie is a window preference (`nightThreads` in prefs.json), task id to thread id, as B39's
// lineage is: the window never writes a task file (`ling night add` does, under the task's lock),
// so the task file itself names no thread. Pure.

import type { NightTask } from "./bridge";

/** Task id to the Work thread it was queued from. */
export type NightThreads = Record<string, string>;

/** At most this many tasks are remembered, the oldest (by id, which starts with when it was queued) dropped first. */
export const NIGHT_THREADS_MAX = 200;

/** A task id as the launcher makes them (`<yyyymmdd>-<hhmm>-<3 hex>`), and as night.ts accepts them. */
const ID = /^[A-Za-z0-9_-]{1,80}$/;

/**
 * Statuses that end a task for good or for the night (ling-rs/src/night.rs `FINISHED`, the runner's
 * `FINAL_STATUSES`): the card shows its result. Others (`queued`, `running`, `sent`,
 * `cancel-requested`, `interrupted`, which the next night picks up again) are still in flight.
 */
export const ENDED = ["done", "no-change", "stalled", "failed", "cancelled"];

/** Only well-formed entries: a task id to a thread id. */
export function cleanNightThreads(stored: unknown): NightThreads {
  const result: NightThreads = {};
  if (typeof stored !== "object" || stored === null || Array.isArray(stored)) return result;
  for (const [id, thread] of Object.entries(stored)) {
    if (ID.test(id) && typeof thread === "string" && thread.length > 0) result[id] = thread;
  }
  return result;
}

/** The task id `ling night add` printed ("Queued <id> on branch night/<id>, …"), or null. */
export function queuedId(said: string): string | null {
  const found = said.match(/^Queued (\S+) on branch /m);
  return found && ID.test(found[1]) ? found[1] : null;
}

/** The tie recorded: the task `ling night add` reported, to `threadId`; unchanged when the output names no task. */
export function tieTask(stored: unknown, said: string, threadId: string): NightThreads {
  const ties = cleanNightThreads(stored);
  const id = queuedId(said);
  if (!id || !threadId) return ties;
  ties[id] = threadId;
  const ids = Object.keys(ties).sort();
  for (const old of ids.slice(0, Math.max(0, ids.length - NIGHT_THREADS_MAX))) delete ties[old];
  return ties;
}

/** The tasks tied to `threadId`, in the order they were queued. */
export function tasksOf(stored: unknown, threadId: string): string[] {
  return Object.entries(cleanNightThreads(stored)).filter(([, thread]) => thread === threadId).map(([id]) => id).sort();
}

/** What a card shows of a task. */
export interface NightCard {
  id: string;
  /** The task's first line (the thread's context follows it in the task's words). */
  title: string;
  status: string;
  /** Whether it has ended (done, no change, stalled, failed, cancelled): the result shows. */
  ended: boolean;
  /** In words: what is happening, or what came of it. */
  state: string;
  /** Attempts, in words, or null before the first. */
  attempts: string | null;
  /** The branch the runner committed (the chosen attempt's, with best-of-N), or null. */
  branch: string | null;
  /** When `ling night apply` brought it into a checkout. */
  applied: string | null;
  /** Apply is offered: there is a branch to bring back. */
  canApply: boolean;
  repo: string;
}

const STATES: Record<string, string> = {
  queued: "Queued for tonight",
  running: "Running now",
  sent: "Handed to another node; its branch comes back with a later run",
  "cancel-requested": "Stopping at its next step",
  interrupted: "Cut off by the end of the window; the next night carries on",
  done: "Done",
  "no-change": "Done, with nothing to change",
  stalled: "Stalled",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** A card for a task as `night/list` read it, or a queued card when the list does not have it yet. */
export function cardOf(id: string, task: NightTask | undefined): NightCard {
  if (!task) {
    return { id, title: id, status: "queued", ended: false, state: STATES.queued, attempts: null, branch: null, applied: null, canApply: false, repo: "" };
  }
  const result = (typeof task.result === "object" && task.result !== null ? task.result : {}) as Record<string, unknown>;
  const best = (typeof result.best_of === "object" && result.best_of !== null ? result.best_of : null) as Record<string, unknown> | null;
  const branch = typeof result.branch === "string" ? result.branch : null;
  const count = typeof result.attempts === "number" ? result.attempts : task.attempts ?? null;
  let attempts: string | null = null;
  if (best && typeof best.n === "number") {
    const ran = typeof best.ran === "number" ? best.ran : null;
    const chosen = typeof best.chosen === "number" ? `, attempt ${best.chosen} chosen` : ran ? ", none chosen" : "";
    attempts = `Best of ${best.n}${ran !== null ? `: ${ran} ran` : ""}${chosen}`;
  } else if (count) {
    attempts = count === 1 ? "1 attempt" : `${count} attempts`;
  }
  const ended = ENDED.includes(task.status);
  return {
    id,
    title: task.task.split("\n")[0].trim() || id,
    status: task.status,
    ended,
    state: STATES[task.status] ?? task.status,
    attempts,
    branch,
    applied: task.applied ?? null,
    canApply: Boolean(branch) && ended,
    repo: task.repo,
  };
}

/** The report's name as a date for the note's heading: `2026-10-28.md` → `2026-10-28`. */
export function reportDate(name: string): string {
  return name.replace(/\.md$/, "");
}

/** The report's block without its `### <id> — <status>` heading, which the card already shows. */
export function noteBody(block: string): string {
  return block.split("\n").filter((line, index) => !(index === 0 && line.startsWith("### "))).join("\n").trim();
}
