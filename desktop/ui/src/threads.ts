// Threads in the sidebar (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1): pins, sections,
// Recents, unread and attention, the order Ctrl+Tab and Ctrl+1–9 walk, and the links that name a
// thread. All window-side state: the protocol has no pin flag, and its thread sections
// (`threadSection/*`) wait for policy.json (tests/test_desktop_parity.py), so sections live in the
// window's preferences for now. Pure.

import type { Thread } from "./protocol/v2/Thread";

export interface Section {
  id: string;
  name: string;
  threads: string[];
}

const recency = (thread: Thread) => thread.recencyAt ?? thread.updatedAt;

/** The most recently active threads, newest first. */
export function recentThreads(threads: Thread[], count = 6): Thread[] {
  return [...threads].sort((a, b) => recency(b) - recency(a)).slice(0, count);
}

/** Pinned first (in pin order), then the rest in the order given. */
export function pinnedFirst<T>(items: T[], pinned: string[], key: (item: T) => string): T[] {
  const at = (item: T) => {
    const index = pinned.indexOf(key(item));
    return index < 0 ? Number.MAX_SAFE_INTEGER : index;
  };
  return items.map((item, index) => ({ item, index })).sort((a, b) => at(a.item) - at(b.item) || a.index - b.index).map((entry) => entry.item);
}

/** `id` moved to the front of `list`, or taken out when it is there and `toggle` is set. */
export function toggled(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((entry) => entry !== id) : [id, ...list];
}

/** The thread after (or before) `current` in `order`, wrapping round. */
export function stepThread(order: string[], current: string | null, direction: 1 | -1): string | null {
  if (order.length === 0) return null;
  const index = current ? order.indexOf(current) : -1;
  if (index < 0) return direction === 1 ? order[0] : order[order.length - 1];
  return order[(index + direction + order.length) % order.length];
}

/**
 * The next thread that needs the user (Ctrl+Alt+A): one with an approval or a question waiting
 * first, then an unread one, in sidebar order after the current one.
 */
export function nextAttention(order: string[], current: string | null, waiting: Set<string>, unread: Set<string>): string | null {
  const start = current ? order.indexOf(current) : -1;
  const rotated = [...order.slice(start + 1), ...order.slice(0, start + 1)].filter((id) => id !== current);
  return rotated.find((id) => waiting.has(id)) ?? rotated.find((id) => unread.has(id)) ?? null;
}

/** Puts `thread` in section `sectionId` (out of any other), or in none with null. */
export function moveToSection(sections: Section[], thread: string, sectionId: string | null): Section[] {
  return sections.map((section) => ({
    ...section,
    threads: section.id === sectionId ? [...section.threads.filter((id) => id !== thread), thread] : section.threads.filter((id) => id !== thread),
  }));
}

/** The link that opens a thread in the app (`ling://threads/<id>`, §2.20). */
export function threadLink(id: string): string {
  return `ling://threads/${encodeURIComponent(id)}`;
}
