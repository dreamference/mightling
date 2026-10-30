// Terminal tabs (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.7): each a PTY the server runs
// with `command/exec` (`tty`, a client-chosen `processId`, no timeout, no output cap) under the
// permission profile the composer shows, so `/airgapped on` holds for what the user types as it
// does for the agent; keystrokes go back with `command/exec/write`, a new size with
// `command/exec/resize`, Close with `command/exec/terminate`. The output arrives as
// `command/exec/outputDelta` notifications, which this hub routes to the tab's view, keeping what
// arrives before the view is there. No PTY code of Mightling's. Pure apart from the RPC client.
//
// In the app every window sees every tab (DESKTOP_ELECTRON §13): a window that draws a tab gets
// what it showed so far from the main process (`seed`), then the output as it comes, so a tab
// drawn in a second window, a detached pane or a pane docked back starts from the beginning.

import type { RpcClient } from "./rpc";
import type { ServerNotification } from "./protocol/ServerNotification";

export interface TerminalSession {
  processId: string;
  /** The thread the tab belongs to; null for one opened with no thread shown. */
  threadId: string | null;
  cwd: string;
  title: string;
  /** The exit code once the shell has ended (null while it runs; -1 when the server refused it). */
  exitCode: number | null;
  error: string | null;
}

/** The command a tab runs: the configured shell as a login shell, else the user's `$SHELL`. */
export function shellCommand(shell: string | null, script: string | null = null): string[] {
  if (script !== null) return shell ? [shell, "-l", "-c", script] : ["/bin/sh", "-c", `exec "\${SHELL:-/bin/bash}" -l -c "$0"`, script];
  return shell ? [shell, "-l"] : ["/bin/sh", "-c", 'exec "${SHELL:-/bin/bash}" -l'];
}

export function encode(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  return btoa(binary);
}

export function decode(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

/** The most kept for a tab with no view yet, and for Send to the thread. */
const PENDING_KEPT = 1024 * 1024;

/** A tab's last `lines` lines of output as plain text, without the terminal's control sequences. */
export function plainTail(text: string, lines = 80): string {
  const plain = text.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, "").replace(/\x1b\][^\x07]*(\x07|\x1b\\)/g, "")
    // Then every other escape (a character set `ESC ( B`, keypad modes `ESC =`, cursor saves `ESC 7`).
    .replace(/\x1b[ -\/]*[0-~]/g, "").replace(/\r\n/g, "\n").replace(/\r/g, "");
  return plain.split("\n").slice(-lines).join("\n").trim();
}

export class TerminalHub {
  private readonly sinks = new Map<string, (bytes: Uint8Array) => void>();
  private readonly pending = new Map<string, Uint8Array[]>();
  /** Each tab's recent output as text, for Send to the thread. */
  private readonly recent = new Map<string, string>();
  /** Each tab's decoder, so a character split across two deltas is kept whole. */
  private readonly decoders = new Map<string, TextDecoder>();
  /** Tabs whose view waits for what the tab showed so far: their output meanwhile is in it. */
  private readonly seeding = new Set<string>();
  private next = 1;

  /**
   * `seed`, in the app, gives a tab's output so far (from the main process); a view attached to a
   * tab is written that first. Without it (a browser) a view gets what arrived before it, as kept.
   */
  constructor(private readonly client: RpcClient, private readonly seed: ((processId: string) => Promise<Uint8Array>) | null = null) {}

  /** Takes a notification; true when it was a terminal's output. */
  deliver(notification: ServerNotification): boolean {
    if (notification.method !== "command/exec/outputDelta") return false;
    const { processId, deltaBase64 } = notification.params;
    const bytes = decode(deltaBase64);
    let decoder = this.decoders.get(processId);
    if (!decoder) this.decoders.set(processId, decoder = new TextDecoder());
    const text = (this.recent.get(processId) ?? "") + decoder.decode(bytes, { stream: true });
    this.recent.set(processId, text.length > PENDING_KEPT ? text.slice(text.length - PENDING_KEPT) : text);
    const sink = this.sinks.get(processId);
    if (sink) sink(bytes);
    else if (!this.seeding.has(processId)) {
      // Kept for a view to come, bounded: the main process keeps the whole of it in the app.
      const kept = [...(this.pending.get(processId) ?? []), bytes];
      let total = kept.reduce((sum, chunk) => sum + chunk.length, 0);
      while (total > PENDING_KEPT && kept.length > 1) total -= (kept.shift() as Uint8Array).length;
      this.pending.set(processId, kept);
    }
    return true;
  }

  /**
   * Connects a tab's view. In the app it is first written what the tab showed so far (`seed`): what
   * arrives before that answer is in it, since the main process sends both in order; in a browser,
   * what arrived before the view.
   */
  attach(processId: string, sink: (bytes: Uint8Array) => void): () => void {
    let attached = true;
    const connect = () => {
      if (!attached) return;
      this.seeding.delete(processId);
      this.sinks.set(processId, sink);
    };
    if (this.seed) {
      this.pending.delete(processId);
      this.seeding.add(processId);
      this.seed(processId).then((bytes) => {
        if (!attached) return;
        if (bytes.length) {
          sink(bytes);
          this.recent.set(processId, new TextDecoder().decode(bytes.length > PENDING_KEPT ? bytes.subarray(bytes.length - PENDING_KEPT) : bytes));
        }
        connect();
      }, connect);
    } else {
      for (const bytes of this.pending.get(processId) ?? []) sink(bytes);
      this.pending.delete(processId);
      connect();
    }
    return () => {
      attached = false;
      this.seeding.delete(processId);
      if (this.sinks.get(processId) === sink) this.sinks.delete(processId);
    };
  }

  /**
   * Starts a shell (or `script`) in `cwd`; resolves at once with the session, and calls `exited`
   * when the process ends (the server answers `command/exec` only then).
   */
  start(options: { threadId: string | null; cwd: string; profile: string | null; shell: string | null; size: { rows: number; cols: number }; title?: string; script?: string | null },
    exited: (processId: string, code: number, error: string | null) => void): TerminalSession {
    const processId = `term-${Date.now().toString(36)}-${this.next++}`;
    const command = shellCommand(options.shell, options.script ?? null);
    this.client.request("command/exec", {
      command, processId, tty: true, disableTimeout: true, disableOutputCap: true, cwd: options.cwd,
      size: options.size, ...(options.profile ? { permissionProfile: options.profile } : {}),
    }).then((answer) => exited(processId, (answer as { exitCode?: number }).exitCode ?? 0, null))
      .catch((error: unknown) => exited(processId, -1, error instanceof Error ? error.message : String(error)));
    return { processId, threadId: options.threadId, cwd: options.cwd, title: options.title ?? "Terminal", exitCode: null, error: null };
  }

  write(processId: string, data: string): Promise<unknown> {
    return this.client.request("command/exec/write", { processId, deltaBase64: encode(data) });
  }

  resize(processId: string, rows: number, cols: number): Promise<unknown> {
    return this.client.request("command/exec/resize", { processId, size: { rows: Math.max(1, rows), cols: Math.max(1, cols) } });
  }

  terminate(processId: string): Promise<unknown> {
    return this.client.request("command/exec/terminate", { processId });
  }

  forget(processId: string): void {
    this.sinks.delete(processId);
    this.pending.delete(processId);
    this.recent.delete(processId);
    this.decoders.delete(processId);
  }

  /** A tab's last `lines` lines of output as plain text, for the composer. */
  tail(processId: string, lines = 80): string {
    return plainTail(this.recent.get(processId) ?? "", lines);
  }

  /** The same, from the whole of what the tab showed when the app keeps it (`seed`); else as `tail`. */
  async tailFrom(processId: string, lines = 80): Promise<string> {
    if (!this.seed) return this.tail(processId, lines);
    const bytes = await this.seed(processId).catch(() => null);
    return bytes && bytes.length ? plainTail(new TextDecoder().decode(bytes), lines) : this.tail(processId, lines);
  }
}

/** A tab's output as a message for the thread (Send to the thread): the agent reads what the terminal shows. */
export function terminalMessage(cwd: string, output: string): string {
  return `Here is what my terminal in ${cwd} shows:\n\n\`\`\`\n${output}\n\`\`\``;
}
