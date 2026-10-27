// Resuming the selected thread when the webview returns from the background (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §10 G10).
// In the web host only, on `document` `visibilitychange` to `"visible"` (and on `pageshow` with `persisted`),
// if a thread is selected and the app-server connection is ready: send `thread/resume` with the same `{ threadId, model }`
// shape as `openThread`, read the newest page of `thread/turns/list` (`limit: 100`), and merge the turns into the store so
// turns that completed while hidden appear. Debounced so at most one resume is in flight at a time.

import type { Host } from "./bridge";
import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";
import type { ServerStatus } from "./store";

export interface ResumeEvent {
  persisted?: boolean;
}

export interface ResumeDocumentLike {
  visibilityState?: string;
  addEventListener: (type: string, listener: (event?: ResumeEvent) => void) => void;
  removeEventListener: (type: string, listener: (event?: ResumeEvent) => void) => void;
}

export interface ResumeWindowLike {
  addEventListener: (type: string, listener: (event?: ResumeEvent) => void) => void;
  removeEventListener: (type: string, listener: (event?: ResumeEvent) => void) => void;
}

export interface ResumeStateSnapshot {
  server: ServerStatus | "connecting" | "closed";
  selectedThreadId: string | null;
  servedModel: string | null;
  thread?: Thread | null;
}

export interface VisibilityResumeOptions {
  host: Host;
  request: (method: string, params: unknown) => Promise<unknown>;
  document: ResumeDocumentLike;
  window?: ResumeWindowLike | null;
  getState: () => ResumeStateSnapshot;
  onThreadOpened: (thread: Thread) => void;
  markLoaded?: (threadId: string) => void;
}

/**
 * Merge turns returned by `thread/turns/list` (already reversed to oldest-first order)
 * into a thread's `turns` array so turns that finished while backgrounded appear.
 */
export function mergeTurnsIntoThread(thread: Thread, listedTurns: readonly Turn[]): Thread {
  if (listedTurns.length === 0) return thread;
  const byId = new Map<string, Turn>();
  const order: string[] = [];
  for (const turn of thread.turns) {
    byId.set(turn.id, turn);
    order.push(turn.id);
  }
  for (const turn of listedTurns) {
    const existing = byId.get(turn.id);
    if (!existing) {
      byId.set(turn.id, turn);
      order.push(turn.id);
    } else {
      byId.set(turn.id, {
        ...existing,
        ...turn,
        items: turn.items.length >= existing.items.length ? turn.items : existing.items,
      });
    }
  }
  return {
    ...thread,
    turns: order.map((id) => byId.get(id)!),
  };
}

/**
 * Attach `visibilitychange` and `pageshow` listeners in the web host to resume the active thread
 * and catch up missed turns. Returns a cleanup function that removes the listeners.
 */
export function attachVisibilityResume(options: VisibilityResumeOptions): () => void {
  const { host, request, document: doc, window: win, getState, onThreadOpened, markLoaded } = options;
  if (host !== "web") {
    return () => {};
  }

  let inFlight = false;

  const runResume = async (): Promise<void> => {
    if (inFlight) return;
    const snapshot = getState();
    if (snapshot.server !== "ready" || !snapshot.selectedThreadId) return;

    const threadId = snapshot.selectedThreadId;
    inFlight = true;
    try {
      const resumed = (await request("thread/resume", {
        threadId,
        model: snapshot.servedModel,
      })) as { thread?: Thread } | null;
      markLoaded?.(threadId);

      // The newest page holds whatever finished while the page was hidden; older turns are known
      // already and stay (the store merges by turn id).
      const turns: Turn[] = [];
      const answer = (await request("thread/turns/list", { threadId, limit: 100, itemsView: "full" })) as { data?: Turn[] } | null;
      if (Array.isArray(answer?.data)) turns.push(...answer.data);
      // Server returns newest first; reverse so oldest is first as Thread.turns expects.
      const ordered = turns.reverse();

      const baseThread = resumed?.thread ?? getState().thread ?? null;
      // Another thread chosen meanwhile: "opened" would select this one again, so it is left alone.
      if (baseThread && getState().selectedThreadId === threadId) {
        onThreadOpened(mergeTurnsIntoThread(baseThread, ordered));
      }
    } catch {
      // Ignore transient RPC errors when returning from background; the socket reconnect handler will retry.
    } finally {
      inFlight = false;
    }
  };

  const onVisibilityChange = () => {
    if (doc.visibilityState !== "visible") return;
    void runResume();
  };

  const onPageShow = (event?: ResumeEvent) => {
    if (!event?.persisted) return;
    void runResume();
  };

  doc.addEventListener("visibilitychange", onVisibilityChange);
  doc.addEventListener("pageshow", onPageShow);
  if (win && win !== doc) {
    win.addEventListener("pageshow", onPageShow);
  }

  return () => {
    doc.removeEventListener("visibilitychange", onVisibilityChange);
    doc.removeEventListener("pageshow", onPageShow);
    if (win && win !== doc) {
      win.removeEventListener("pageshow", onPageShow);
    }
  };
}
