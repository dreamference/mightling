// Threads from other devices (specs/DREAMFERENCE_MIGHTLING_SYNC.md, Phase 1): on `ling web` with
// the node's sync hub on, the page lists the threads every paired device published, reads one as a
// transcript and, with `control`, archives or deletes it everywhere. Read only otherwise: a turn
// runs on the device that holds the code (§0 decision 4); following and answering live is Phase 2.
// Same-origin requests to `/api/sync/`, with the page's own cookie; no app-server method, so the
// bridge policy is unchanged. The desktop app has no hub of its own and shows none of this yet.

/** A thread as the hub lists it (ling-rs/web/src/sync_store.rs `ThreadView::listed`). */
export interface SyncedThread {
  thread: string;
  owner: string;
  owner_name: string;
  title: string;
  archived: boolean;
  cwd: string | null;
  source: string | null;
  created: number;
  updated: number;
  fork_of: string | null;
}

/** One entry of a transcript (`items_of`). */
export interface SyncedItem {
  kind: "user" | "agent" | "command" | "files" | "tool" | "turn";
  text: string;
  status?: string | null;
  exit_code?: number | null;
  output?: string;
  at?: string | null;
}

export interface SyncStatus {
  enabled: boolean;
  cursor: number;
  /** This machine's own sync agent: its threads are the page's own, listed by the app-server. */
  this_device: string | null;
  caller: { device: string; agent: boolean; scope: "control" | "watch" };
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

async function json<T>(answer: Response): Promise<T> {
  const value = await answer.json().catch(() => ({}));
  if (!answer.ok) throw new Error(typeof value?.error === "string" ? value.error : `the hub answered ${answer.status}`);
  return value as T;
}

/** The hub's routes, over `fetch` (a stand-in in the tests). */
export function hub(fetcher: Fetch = (input, init) => fetch(input, init)) {
  return {
    /** The hub's state, or null when it is off (every route answers 404 then) or unreachable. */
    status: async (): Promise<SyncStatus | null> => {
      const answer = await fetcher("/api/sync/status").catch(() => null);
      if (!answer || !answer.ok) return null;
      return answer.json() as Promise<SyncStatus>;
    },
    threads: () => fetcher("/api/sync/threads").then((answer) => json<{ cursor: number; this_device: string | null; threads: SyncedThread[] }>(answer)),
    read: (thread: string) => fetcher(`/api/sync/threads/${encodeURIComponent(thread)}`).then((answer) => json<{ thread: SyncedThread; items: SyncedItem[] }>(answer)),
    /** Resolves once anything changed after `cursor`, or after `wait` seconds; with the new cursor. */
    changed: (cursor: number, wait = 25) => fetcher(`/api/sync/manifest?since=${cursor}&wait=${wait}`).then((answer) => json<{ cursor: number }>(answer)).then((value) => value.cursor),
    archive: (thread: string, archived: boolean) => fetcher(`/api/sync/threads/${encodeURIComponent(thread)}/meta`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived }),
    }).then((answer) => json<unknown>(answer)),
    remove: (thread: string) => fetcher(`/api/sync/threads/${encodeURIComponent(thread)}/delete`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    }).then((answer) => json<unknown>(answer)),
  };
}

/** Other devices' threads, grouped by device in the order of each device's newest thread. */
export function byDevice(threads: SyncedThread[], thisDevice: string | null, archived: boolean): { device: string; name: string; threads: SyncedThread[] }[] {
  const groups = new Map<string, { device: string; name: string; threads: SyncedThread[] }>();
  const shown = threads.filter((thread) => thread.owner !== thisDevice && thread.archived === archived).sort((a, b) => b.updated - a.updated);
  for (const thread of shown) {
    const group = groups.get(thread.owner) ?? { device: thread.owner, name: thread.owner_name || thread.owner, threads: [] };
    group.threads.push(thread);
    groups.set(thread.owner, group);
  }
  return [...groups.values()];
}

/** How many threads other devices have that this page would list. */
export function elsewhere(threads: SyncedThread[], thisDevice: string | null): number {
  return threads.filter((thread) => thread.owner !== thisDevice && !thread.archived).length;
}

/** "3 min ago" for a time in seconds. */
export function ago(seconds: number, now = Date.now() / 1000): string {
  const elapsed = Math.max(0, Math.round(now - seconds));
  if (elapsed < 60) return "just now";
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)} min ago`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)} h ago`;
  return `${Math.floor(elapsed / 86400)} days ago`;
}

/** The last part of a folder, for a thread's line. */
export function folderName(cwd: string | null): string {
  return cwd ? (cwd.split(/[\\/]/).filter(Boolean).pop() ?? cwd) : "";
}
