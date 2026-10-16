// Several windows on the one app-server (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1
// "Pop-out"): a thread in a window of its own talks to the same `ling app-server` as the main
// window, so no second agent process ever starts. Each window numbers its requests from 1, so this
// process tags every request id with its window and gives each answer back to the window that asked,
// untagged; notifications and the server's own requests go to every window (an approval answered
// in one is resolved in all by `serverRequest/resolved`). The protocol takes one `initialize` per
// connection: the first window's is sent, later windows get its answer from here. No Electron here.

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
