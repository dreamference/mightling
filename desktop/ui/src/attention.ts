// Activity's main tab (DESKTOP_PARITY §4.5 item 17, decided by the owner on 2026-10-21): the threads
// that need the user, as the vendor's Activity view lists them: waiting for an answer (an approval or a
// question), running, or unread; filtered by Work, Ask, Pinned and Scheduled (Night Shift's finished
// tasks not yet seen). Mark all as read clears the unread marks and Night Shift's count. Pure.

import type { Thread } from "./protocol/v2/Thread";

export type AttentionState = "waiting" | "running" | "unread";
export type AttentionFilter = "work" | "ask" | "pinned";

export interface AttentionEntry {
  thread: Thread;
  /** Why it is listed, the most pressing first. */
  states: AttentionState[];
  ask: boolean;
  pinned: boolean;
}

const WEIGHT: Record<AttentionState, number> = { waiting: 0, running: 1, unread: 2 };

/**
 * The threads that need the user: waiting first, then running, then unread, each group newest first.
 * Ephemeral threads and subagents' are left out (their parent is what the user follows).
 */
export function attentionList(
  threads: Thread[],
  facts: { waiting: Set<string>; running: Set<string>; unread: Set<string>; pinned: Set<string>; isAsk: (thread: Thread) => boolean },
): AttentionEntry[] {
  const entries: AttentionEntry[] = [];
  for (const thread of threads) {
    if (thread.ephemeral || thread.parentThreadId) continue;
    const states = (["waiting", "running", "unread"] as const).filter((state) => facts[state].has(thread.id));
    if (states.length === 0) continue;
    entries.push({ thread, states: [...states], ask: facts.isAsk(thread), pinned: facts.pinned.has(thread.id) });
  }
  const recency = (thread: Thread) => thread.recencyAt ?? thread.updatedAt;
  return entries.sort((a, b) => WEIGHT[a.states[0]] - WEIGHT[b.states[0]] || recency(b.thread) - recency(a.thread));
}

/** The entries a set of filters keeps: none chosen keeps all; several keep what any of them keeps. */
export function filtered(entries: AttentionEntry[], filters: Set<AttentionFilter>): AttentionEntry[] {
  if (filters.size === 0) return entries;
  return entries.filter((entry) => (filters.has("work") && !entry.ask) || (filters.has("ask") && entry.ask) || (filters.has("pinned") && entry.pinned));
}

/** A state, in words. */
export const STATE_LABELS: Record<AttentionState, string> = { waiting: "Waiting for you", running: "Working", unread: "Unread" };
