// Several windows on the one app-server (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §13; the
// pop-out of DESKTOP_PARITY §2.1 was the first): every window, a full one, a thread popped out or a
// detached pane, talks to the same `ling app-server`, so no second agent process ever starts. Each
// window numbers its requests from 1, so this process tags every request id with its window and
// gives each answer back to the window that asked, untagged; notifications and the server's own
// requests go to every window (an approval answered in one is resolved in all by
// `serverRequest/resolved`). The protocol takes one `initialize` per connection: the first window's
// is sent, later windows get its answer from here.
//
// A thread shown in two windows has one composer (`WindowHub`): the window that showed it first, or
// took it over, owns it; the others mirror the thread read-only. This process refuses a turn, a
// steer, an interrupt, a queue change or an answer to the thread's approvals from any other window,
// so two windows can never both send. No Electron here: windows.test.ts runs the routing cases of
// windows.vectors.json through it and the policy together.

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

const SEPARATOR = "␟";

export type Routed = { to: "all" } | { to: string };

export class RequestRouter {
  private initialized: unknown = null;

  /**
   * A window's outgoing message, ready for the server: a request's id tagged with the window;
   * `null` when this process answers it itself (a later window's `initialize`), with `reply` holding the answer.
   */
  outgoing(window: string, message: unknown): { send: unknown | null; reply: unknown | null } {
    if (!isObject(message)) return { send: message, reply: null };
    const method = typeof message.method === "string" ? message.method : null;
    if (method === "initialize" && "id" in message && this.initialized !== null) {
      return { send: null, reply: { id: message.id, result: this.initialized } };
    }
    if (method === "initialized" && !("id" in message) && this.initialized !== null && window !== this.first) {
      return { send: null, reply: null };
    }
    if (method !== null && "id" in message) {
      const tagged = `${window}${SEPARATOR}${JSON.stringify(message.id)}`;
      if (method === "initialize") {
        this.first = window;
        this.initializeId = tagged;
      }
      return { send: { ...message, id: tagged }, reply: null };
    }
    return { send: message, reply: null };
  }

  private first: string | null = null;
  private initializeId: string | null = null;

  /** Where a message from the server goes, and as what. */
  incoming(message: unknown): { to: string | "all"; payload: unknown } {
    if (!isObject(message) || typeof message.method === "string" || !("id" in message) || typeof message.id !== "string") {
      return { to: "all", payload: message };
    }
    const at = message.id.indexOf(SEPARATOR);
    if (at < 0) return { to: "all", payload: message };
    const window = message.id.slice(0, at);
    let id: unknown;
    try {
      id = JSON.parse(message.id.slice(at + SEPARATOR.length));
    } catch {
      return { to: "all", payload: message };
    }
    if (message.id === this.initializeId && "result" in message) this.initialized = message.result;
    return { to: window, payload: { ...message, id } };
  }

  /** The server went away: the next window to initialize sends its own `initialize` again. */
  reset(): void {
    this.initialized = null;
    this.first = null;
    this.initializeId = null;
  }
}

/** A full window (Ask and Work), a thread popped out into a window of its own, or a detached pane. */
export type WindowKind = "full" | "popout" | "pane";

/**
 * What only the composer's window may send for a thread: what starts, steers or stops a turn, or
 * changes what the thread will do next. Reading a thread, naming or archiving it, and terminals
 * (`command/exec*`, which name a process, not a thread) stay open to every window.
 */
export const COMPOSER_METHODS: ReadonlySet<string> = new Set([
  "turn/start",
  "turn/steer",
  "turn/interrupt",
  "review/start",
  "thread/compact/start",
  "thread/revert",
  "thread/queue/add",
  "thread/queue/update",
  "thread/queue/delete",
  "thread/queue/reorder",
  "thread/queue/start",
  "thread/goal/set",
  "thread/goal/clear",
]);

/** What each window is told about the composers (the `windows` message): itself, each thread's owner, and who answers for the rest. */
export interface WindowsState {
  /** This window's id. */
  self: string;
  /** Each thread shown somewhere, and the window whose composer it is. */
  owners: Record<string, string>;
  /** The window that answers the server's requests for a thread no window owns (the most recent full window), or null. */
  fallback: string | null;
}

interface Entry {
  kind: WindowKind;
  /** The thread the window shows, or null. */
  thread: string | null;
}

const threadOf = (message: Json): string | null => {
  const params = isObject(message.params) ? message.params : null;
  return params && typeof params.threadId === "string" ? params.threadId : null;
};

export class WindowHub {
  private readonly router = new RequestRouter();
  private readonly windows = new Map<string, Entry>();
  /** Window ids, least recently focused first. */
  private order: string[] = [];
  private readonly owners = new Map<string, string>();
  /** The server's requests waiting for an answer, by id (as JSON), with the thread each is about. */
  private readonly asked = new Map<string, string>();

  /** A window opened. */
  open(id: string, kind: WindowKind): void {
    this.windows.set(id, { kind, thread: null });
    this.order = [...this.order.filter((entry) => entry !== id), id];
  }

  /** A window closed: its threads pass to the most recent window still showing them, or to none. */
  close(id: string): void {
    this.windows.delete(id);
    this.order = this.order.filter((entry) => entry !== id);
    for (const [thread, owner] of [...this.owners]) if (owner === id) this.release(thread);
  }

  /** A window came to the front. */
  focus(id: string): void {
    if (!this.windows.has(id)) return;
    this.order = [...this.order.filter((entry) => entry !== id), id];
  }

  /**
   * A window shows `thread` (or none). It owns the thread's composer when no window does, or when
   * it asks to take it (`take`: a thread popped out, or "Use the composer here"). A pane never owns one.
   */
  show(id: string, thread: string | null, take = false): void {
    const entry = this.windows.get(id);
    if (!entry || entry.kind === "pane") return;
    const before = entry.thread;
    entry.thread = thread;
    if (before !== null && before !== thread && this.owners.get(before) === id) this.release(before);
    if (thread === null) return;
    if (take || !this.owners.has(thread)) this.owners.set(thread, id);
  }

  /** The window whose composer `thread` is, or null when no window shows it. */
  owner(thread: string): string | null {
    return this.owners.get(thread) ?? null;
  }

  /** The thread a window shows. */
  shown(id: string): string | null {
    return this.windows.get(id)?.thread ?? null;
  }

  kind(id: string): WindowKind | null {
    return this.windows.get(id)?.kind ?? null;
  }

  /** The most recently focused window of one of `kinds`, or null. */
  mostRecent(kinds: readonly WindowKind[] = ["full"]): string | null {
    for (let index = this.order.length - 1; index >= 0; index--) {
      const id = this.order[index];
      const entry = this.windows.get(id);
      if (entry && kinds.includes(entry.kind)) return id;
    }
    return null;
  }

  /** What window `id` is told. */
  state(id: string): WindowsState {
    return { self: id, owners: Object.fromEntries(this.owners), fallback: this.mostRecent(["full", "popout"]) };
  }

  private release(thread: string): void {
    this.owners.delete(thread);
    for (let index = this.order.length - 1; index >= 0; index--) {
      const id = this.order[index];
      const entry = this.windows.get(id);
      if (entry && entry.kind !== "pane" && entry.thread === thread) {
        this.owners.set(thread, id);
        return;
      }
    }
  }

  /**
   * A window's message for the server, routed (`RequestRouter.outgoing`); throws when the window
   * may not send it: a composer method or an answer about a thread whose composer is elsewhere.
   */
  outgoing(id: string, message: unknown): { send: unknown | null; reply: unknown | null } {
    const entry = this.windows.get(id);
    if (!entry) throw new Error("only the app's windows talk to the agent");
    if (isObject(message)) {
      const method = typeof message.method === "string" ? message.method : null;
      if (method !== null && COMPOSER_METHODS.has(method)) {
        if (entry.kind === "pane") throw new Error(`a detached pane has no composer: ${method} is sent from the thread's window`);
        const thread = threadOf(message);
        const owner = thread === null ? null : this.owners.get(thread);
        if (thread !== null && owner !== undefined && owner !== null && owner !== id) {
          throw new Error(`the composer of this thread is in another window: ${method} is sent from there`);
        }
      }
      if (method === null && "id" in message && ("result" in message || "error" in message)) {
        const key = JSON.stringify(message.id);
        const thread = this.asked.get(key);
        if (thread !== undefined) {
          const owner = this.owners.get(thread);
          if (entry.kind === "pane" || (owner !== undefined && owner !== id)) throw new Error("the composer of this thread is in another window: it answers there");
          this.asked.delete(key);
        }
      }
    }
    return this.router.outgoing(id, message);
  }

  /** Where a message from the server goes, and as what; remembers which thread each server request is about. */
  incoming(message: unknown): { to: string | "all"; payload: unknown } {
    if (isObject(message) && typeof message.method === "string") {
      if ("id" in message) {
        const thread = threadOf(message);
        if (thread !== null) this.asked.set(JSON.stringify(message.id), thread);
      } else if (message.method === "serverRequest/resolved" && isObject(message.params) && "requestId" in message.params) {
        this.asked.delete(JSON.stringify(message.params.requestId));
      }
    }
    return this.router.incoming(message);
  }

  /** The server went away: routing starts again; who shows what stays. */
  reset(): void {
    this.router.reset();
    this.asked.clear();
  }
}

/** A pane that detaches into a window of its own: the terminal tabs, or the review pane's diff. */
export type PaneKind = "terminal" | "review";

export const PANE_KINDS: readonly PaneKind[] = ["terminal", "review"];

/**
 * What a detached pane asks of the window it came from, which holds the thread's composer and its
 * permissions: a new terminal tab for the thread, text for the composer (comments on the diff, a
 * terminal's output), a file opened in a tab.
 */
export type PaneAction =
  | { kind: "newTerminal"; thread: string }
  | { kind: "compose"; thread: string; text: string }
  | { kind: "openFile"; thread: string; path: string; line: number | null };

/** The most text a pane hands to the composer at once. */
export const PANE_TEXT_MAX = 256 * 1024;

/** A pane's request, checked, for the thread the pane shows (never another). */
export function paneAction(value: unknown, thread: string): PaneAction {
  if (!isObject(value)) throw new Error("a pane's request must be an object");
  switch (value.kind) {
    case "newTerminal":
      return { kind: "newTerminal", thread };
    case "compose":
      if (typeof value.text !== "string" || value.text.length > PANE_TEXT_MAX) throw new Error("text for the composer must be a string of at most 256 KB");
      return { kind: "compose", thread, text: value.text };
    case "openFile": {
      if (typeof value.path !== "string" || !value.path || value.path.length > 4096) throw new Error("a file to open must be named");
      const line = typeof value.line === "number" && Number.isInteger(value.line) && value.line > 0 ? value.line : null;
      return { kind: "openFile", thread, path: value.path, line };
    }
    default:
      throw new Error("a pane asks for a new terminal, text for the composer or a file, nothing else");
  }
}
