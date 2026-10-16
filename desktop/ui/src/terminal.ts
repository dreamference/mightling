// Terminal tabs (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.7): each a PTY the server runs
// with `command/exec` (`tty`, a client-chosen `processId`, no timeout, no output cap) under the
// permission profile the composer shows, so `/airgapped on` holds for what the user types as it
// does for the agent; keystrokes go back with `command/exec/write`, a new size with
// `command/exec/resize`, Close with `command/exec/terminate`. The output arrives as
// `command/exec/outputDelta` notifications, which this hub routes to the tab's view, keeping what
// arrives before the view is there. No PTY code of Mightling's. Pure apart from the RPC client.

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

export class TerminalHub {
  private readonly sinks = new Map<string, (bytes: Uint8Array) => void>();
  private readonly pending = new Map<string, Uint8Array[]>();
  /** Each tab's recent output as text, for Send to the thread. */
  private readonly recent = new Map<string, string>();
  private next = 1;

  constructor(private readonly client: RpcClient) {}

  /** Takes a notification; true when it was a terminal's output. */
  deliver(notification: ServerNotification): boolean {
    if (notification.method !== "command/exec/outputDelta") return false;
    const { processId, deltaBase64 } = notification.params;
    const bytes = decode(deltaBase64);
    const text = (this.recent.get(processId) ?? "") + new TextDecoder().decode(bytes);
    this.recent.set(processId, text.length > PENDING_KEPT ? text.slice(text.length - PENDING_KEPT) : text);
    const sink = this.sinks.get(processId);
    if (sink) sink(bytes);
    else this.pending.set(processId, [...(this.pending.get(processId) ?? []), bytes]);
    return true;
  }

  /** Connects a tab's view; what arrived before it is written first. */
  attach(processId: string, sink: (bytes: Uint8Array) => void): () => void {
    for (const bytes of this.pending.get(processId) ?? []) sink(bytes);
    this.pending.delete(processId);
    this.sinks.set(processId, sink);
    return () => {
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
  }

  /** A tab's last `lines` lines of output as plain text, for the composer. */
  tail(processId: string, lines = 80): string {
    // Without the terminal's control sequences.
    const plain = (this.recent.get(processId) ?? "").replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, "").replace(/\x1b\][^\x07]*(\x07|\x1b\\)/g, "").replace(/\r\n/g, "\n").replace(/\r/g, "");
    return plain.split("\n").slice(-lines).join("\n").trim();
  }
}

/** A tab's output as a message for the thread (Send to the thread): the agent reads what the terminal shows. */
export function terminalMessage(cwd: string, output: string): string {
  return `Here is what my terminal in ${cwd} shows:\n\n\`\`\`\n${output}\n\`\`\``;
}
