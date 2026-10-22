// The terminal tabs every window sees (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §13): a
// terminal is a PTY the app-server runs (`command/exec`, desktop/ui/src/terminal.ts), started by
// one window, and the server answers its exit to that window alone. So the window that starts a
// tab publishes it here, and its exit when it comes; any window may close one; this process tells
// every window the whole list, and keeps each tab's recent output (it sees every
// `command/exec/outputDelta` on its way to the windows), so a window that shows a tab it did not
// start (a detached terminal pane, a second window, a pane docked back) draws it from the start.
// No Electron here.

export interface TerminalSession {
  processId: string;
  threadId: string | null;
  cwd: string;
  title: string;
  exitCode: number | null;
  error: string | null;
}

/** The most output kept per tab, as the page's own hub keeps it. */
export const BACKLOG_BYTES = 256 * 1024;
/** The most tabs kept; the oldest go first. */
export const MAX_TERMINALS = 64;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

/** A published tab, checked field by field; anything else is refused. */
export function sessionOf(value: unknown): TerminalSession {
  if (!isObject(value)) throw new Error("a terminal must be an object");
  const { processId, threadId, cwd, title, exitCode, error } = value;
  if (typeof processId !== "string" || !/^[A-Za-z0-9._-]{1,80}$/.test(processId)) throw new Error("a terminal needs a process id");
  if (threadId !== null && typeof threadId !== "string") throw new Error("a terminal's thread must be a string or null");
  if (typeof cwd !== "string" || typeof title !== "string") throw new Error("a terminal needs a folder and a title");
  if (exitCode !== null && typeof exitCode !== "number") throw new Error("a terminal's exit code must be a number or null");
  if (error !== null && error !== undefined && typeof error !== "string") throw new Error("a terminal's error must be a string or null");
  return { processId, threadId: threadId as string | null, cwd: cwd.slice(0, 4096), title: title.slice(0, 200), exitCode: exitCode as number | null, error: (error as string | null | undefined)?.slice(0, 2000) ?? null };
}

export class TerminalMirror {
  private readonly sessions = new Map<string, TerminalSession>();
  private readonly output = new Map<string, Buffer[]>();

  /** Adds or updates a tab; true when the list changed. */
  publish(value: unknown): boolean {
    const session = sessionOf(value);
    const before = this.sessions.get(session.processId);
    if (before && JSON.stringify(before) === JSON.stringify(session)) return false;
    this.sessions.delete(session.processId);
    this.sessions.set(session.processId, session);
    while (this.sessions.size > MAX_TERMINALS) this.forget(this.sessions.keys().next().value as string);
    return true;
  }

  /** A tab closed; true when it was listed. */
  forget(processId: unknown): boolean {
    if (typeof processId !== "string") return false;
    this.output.delete(processId);
    return this.sessions.delete(processId);
  }

  /** Notes a message from the server: a terminal's output is kept, the rest ignored. */
  observe(message: unknown): void {
    if (!isObject(message) || message.method !== "command/exec/outputDelta" || !isObject(message.params)) return;
    const { processId, deltaBase64 } = message.params;
    if (typeof processId !== "string" || typeof deltaBase64 !== "string") return;
    const chunks = this.output.get(processId) ?? [];
    if (!this.output.has(processId)) {
      this.output.set(processId, chunks);
      // Output of tabs nobody published (yet) is kept too, within the same bound on the count.
      while (this.output.size > MAX_TERMINALS) this.output.delete(this.output.keys().next().value as string);
    }
    chunks.push(Buffer.from(deltaBase64, "base64"));
    let total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    while (total > BACKLOG_BYTES && chunks.length > 1) total -= (chunks.shift() as Buffer).length;
    if (total > BACKLOG_BYTES) chunks[0] = chunks[0].subarray(chunks[0].length - BACKLOG_BYTES);
  }

  /** Every tab, oldest first. */
  list(): TerminalSession[] {
    return [...this.sessions.values()];
  }

  /** A tab's recent output, base64. */
  backlog(processId: unknown): string {
    if (typeof processId !== "string") return "";
    return Buffer.concat(this.output.get(processId) ?? []).toString("base64");
  }

  /** The server went away, and its terminals with it. */
  clear(): void {
    this.sessions.clear();
    this.output.clear();
  }
}
