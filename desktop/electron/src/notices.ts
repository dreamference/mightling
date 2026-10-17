// Desktop notifications (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1, §2.15): which of the
// server's messages become a notification under the window's preferences: a turn that completed
// (each thread's own, never a throwaway thread's such as a commit message's), an approval or a
// question waiting. A click brings the window forward on that thread. No Electron here.

export interface NoticePrefs {
  notifyTurns?: unknown;
  notifyApprovals?: unknown;
}

export interface Notice {
  title: string;
  body: string;
  threadId: string | null;
  /** Shown even when the window is in front. */
  always: boolean;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

export class Notices {
  /** Throwaway threads (`ephemeral`) and subagents, seen as they open: never notified. */
  private readonly ephemeral = new Set<string>();

  /**
   * Reads one message from the server; returns the notification it calls for, if any. `prefs` is
   * read only for a message that may notify, since this runs for every streamed delta.
   */
  take(message: unknown, readPrefs: () => NoticePrefs): Notice | null {
    if (!isObject(message)) return null;
    const result = isObject(message.result) ? message.result : null;
    const opened = result && isObject(result.thread) ? result.thread : null;
    if (opened && opened.ephemeral === true && typeof opened.id === "string") this.ephemeral.add(opened.id);
    // A subagent's turns are its parent's business: only the parent's end is announced.
    if (message.method === "thread/started" && isObject(message.params) && isObject(message.params.thread)) {
      const started = message.params.thread;
      if ((started.ephemeral === true || typeof started.parentThreadId === "string") && typeof started.id === "string") this.ephemeral.add(started.id);
    }
    const method = typeof message.method === "string" ? message.method : null;
    const params = isObject(message.params) ? message.params : {};
    const threadId = typeof params.threadId === "string" ? params.threadId : null;
    if (method === "turn/completed" && threadId && !this.ephemeral.has(threadId)) {
      const prefs = readPrefs();
      if (prefs.notifyTurns === "never") return null;
      const turn = isObject(params.turn) ? params.turn : {};
      const status = turn.status === "failed" ? "stopped with an error" : turn.status === "interrupted" ? "was stopped" : "finished";
      return { title: "Mightling", body: `A turn ${status}.`, threadId, always: prefs.notifyTurns === "always" };
    }
    if (method && "id" in message && /requestApproval$|requestUserInput$|^execCommandApproval$|^applyPatchApproval$/.test(method)) {
      if (readPrefs().notifyApprovals === false) return null;
      const what = method.includes("UserInput") ? "a question" : "an approval";
      return { title: "Mightling", body: `The agent is waiting for ${what}.`, threadId, always: false };
    }
    return null;
  }
}
