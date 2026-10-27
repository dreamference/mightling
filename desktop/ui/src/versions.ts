// Earlier versions of an edited message (DESKTOP §16 B39, DESKTOP_PARITY_CLAUDE gap 5). Editing a
// message (Edit on the last one, Edit in a new thread on any) forks the thread before it
// (`thread/fork` with `beforeTurnId`), so the message as it was sent stays in the thread it was sent
// in, and the new words are sent in the fork. Each version of a message is a thread; the message
// shows `‹ 2 / 3 ›`, and switching opens that version's thread where it was left. Nothing is deleted.
//
// The lineage is a window preference (`messageVersions` in prefs.json): one entry per edited message,
// keyed `<thread>:<index>` (the thread the first version was sent in, and the message's turn index,
// which a fork before it keeps), its value the versions' threads in the order they were made. Every
// version shares the turns before the message with the first, so a turn before it in a later
// version is the first version's turn: an edit there joins (or starts) the first version's entry.
// "Fork from here" is a copy, not an edit, and is not recorded. Pure.

import { lastUserTurn } from "./composer";
import type { ThreadView, TurnView } from "./store";

/** The lineage as stored: `<thread>:<index>` to the versions' threads, the first version first. */
export type Lineage = Record<string, string[]>;

/** At most this many edited messages are remembered, the oldest dropped first; each has at most `VERSIONS_MAX`. */
export const LINEAGES_MAX = 200;
export const VERSIONS_MAX = 50;

/** One message's versions as the thread shows them: which one this is (1-based), how many, and their threads. */
export interface MessageVersions {
  at: number;
  of: number;
  threads: string[];
}

const keyOf = (threadId: string, index: number) => `${threadId}:${index}`;

/** The entry's message index, or null for a key that is not `<thread>:<index>`. */
function indexOf(key: string): number | null {
  const at = key.lastIndexOf(":");
  if (at <= 0) return null;
  const text = key.slice(at + 1);
  return /^\d+$/.test(text) ? Number(text) : null;
}

/** Only well-formed entries: a `<thread>:<index>` key, two or more distinct thread ids, the key's thread first. */
export function cleanLineage(stored: unknown): Lineage {
  const result: Lineage = {};
  if (typeof stored !== "object" || stored === null || Array.isArray(stored)) return result;
  for (const [key, threads] of Object.entries(stored)) {
    if (indexOf(key) === null || !Array.isArray(threads)) continue;
    if (!threads.every((id) => typeof id === "string" && id.length > 0) || new Set(threads).size !== threads.length) continue;
    if (threads.length < 2 || `${threads[0]}:${indexOf(key)}` !== key) continue;
    result[key] = threads as string[];
  }
  return result;
}

/**
 * The thread whose message at `index` this thread shows: itself, unless it is a later version of a
 * message after `index` (its turns before that message are the first version's), followed back.
 */
function owner(lineage: Lineage, threadId: string, index: number): string {
  let current = threadId;
  for (let step = 0; step < 1000; step++) {
    const entry = Object.entries(lineage).find(([key, threads]) => (indexOf(key) ?? -1) > index && threads.indexOf(current) > 0);
    if (!entry) return current;
    current = entry[1][0];
  }
  return current;
}

/** The entry that holds the message `index` of `threadId` (as its first version or a later one). */
function entryAt(lineage: Lineage, threadId: string, index: number): [string, string[]] | null {
  const thread = owner(lineage, threadId, index);
  return Object.entries(lineage).find(([key, threads]) => indexOf(key) === index && threads.includes(thread)) ?? null;
}

/**
 * The lineage with a new version of the message `index` of `threadId`: `forkId`, the fork made before
 * it. An edit of a version joins that message's entry, so a third edit shows `‹ 3 / 3 ›`.
 */
export function recordVersion(stored: Lineage, threadId: string, index: number, forkId: string): Lineage {
  const lineage = cleanLineage(stored);
  if (forkId === threadId || index < 0) return lineage;
  const found = entryAt(lineage, threadId, index);
  const [key, threads] = found ?? [keyOf(owner(lineage, threadId, index), index), [owner(lineage, threadId, index)]];
  if (threads.includes(forkId)) return lineage;
  const next: Lineage = {};
  for (const [other, versions] of Object.entries(lineage)) if (other !== key) next[other] = versions;
  // Kept at the end, the newest; a full entry drops its second version, never the first (the key's).
  next[key] = [...threads, forkId].filter((_, position, all) => position === 0 || position > all.length - VERSIONS_MAX);
  const keys = Object.keys(next);
  for (const old of keys.slice(0, Math.max(0, keys.length - LINEAGES_MAX))) delete next[old];
  return next;
}

/** The versions of the message `index` of `threadId`, or null when it has only the one. */
export function versionsAt(stored: Lineage, threadId: string, index: number): MessageVersions | null {
  const lineage = cleanLineage(stored);
  const found = entryAt(lineage, threadId, index);
  if (!found) return null;
  const threads = found[1];
  const at = threads.indexOf(owner(lineage, threadId, index));
  return at < 0 ? null : { at: at + 1, of: threads.length, threads };
}

/** The thread `step` versions away (-1 the previous, +1 the next), or null past either end. */
export function stepVersion(versions: MessageVersions, step: number): string | null {
  const to = versions.at - 1 + step;
  return to >= 0 && to < versions.threads.length ? versions.threads[to] : null;
}

/** The turn Edit (Esc Esc, `/edit`) makes a new version of: the last message's, or null while a turn runs or with none. */
export function editedTurn(view: ThreadView): TurnView | null {
  if (view.activeTurnId) return null;
  const last = lastUserTurn(view);
  return (last && view.turns.find((turn) => turn.id === last.turnId)) || null;
}
