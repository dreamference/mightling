// The composer's drafts, one per thread (and one per view for a thread not started yet): text typed
// in one thread showed up in every other, and a new thread's composer could come up holding the
// first message of the thread it had just started (the 1.7.0 Mac test).

/** The draft's key: the thread's id, or the view's own key before a thread exists. */
export function draftKeyOf(view: string, threadId: string | null): string {
  return threadId ? `thread:${threadId}` : `new:${view}`;
}

/** `all` with `key`'s draft set to `value` (an empty draft is dropped); the same object when nothing changes. */
export function withDraft(all: Record<string, string>, key: string, value: string | ((current: string) => string)): Record<string, string> {
  const current = all[key] ?? "";
  const next = typeof value === "function" ? value(current) : value;
  if (next === current) return all;
  const copy = { ...all };
  if (next) copy[key] = next;
  else delete copy[key];
  return copy;
}
