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

/** How long a thread name may be (the TUI's /rename and the side chat's names keep to it). */
export const NAME_MAX = 120;

/**
 * What the rename box starts with: the title the sidebar shows, its name or else its first
 * question, never a placeholder such as "(new thread)" and never empty for a titled thread (the
 * vendor's app 26.917: titles stay intact in the Rename dialog).
 */
export function renameStart(thread: Pick<Thread, "name" | "preview">): string {
  return (thread.name || thread.preview || "").replace(/\s+/g, " ").trim().slice(0, NAME_MAX);
}

/** The link that opens a thread in the app (`ling://threads/<id>`, §2.20). */
export function threadLink(id: string): string {
  return `ling://threads/${encodeURIComponent(id)}`;
}

/** How many pages of 200 the sidebar reads at most (10,000 threads); one more page waits for a search. */
export const THREAD_PAGES = 50;

/**
 * Reads `thread/list` page by page (its cursor; DESKTOP_PARITY §4.5 item 25, DESKTOP §16 B10), handing
 * each page over as it comes, so a project whose threads are all older than the first 200 is still
 * listed. Stops at `maxPages` or when `stopped()` says the window moved on.
 */
export async function readThreadPages(
  list: (cursor: string | null) => Promise<{ data: Thread[]; nextCursor: string | null }>,
  onPage: (threads: Thread[], page: number) => void,
  maxPages = THREAD_PAGES,
  stopped: () => boolean = () => false,
): Promise<{ pages: number; complete: boolean }> {
  let cursor: string | null = null;
  for (let page = 0; page < maxPages; page++) {
    const answer = await list(cursor);
    if (stopped()) return { pages: page, complete: false };
    onPage(answer.data, page);
    cursor = answer.nextCursor;
    if (!cursor) return { pages: page + 1, complete: true };
  }
  return { pages: maxPages, complete: false };
}

/** Every top-level thread by date, newest first, across projects: the sidebar's Chronological list. */
export function chronological(threads: Thread[]): Thread[] {
  return [...threads].sort((a, b) => recency(b) - recency(a));
}

