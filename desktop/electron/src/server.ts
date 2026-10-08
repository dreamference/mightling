// The connection to `ling app-server` (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §4.3), the process
// half of the bridge. One server per app, started through the launcher (`ling app-server`, never a
// bare `codex`) when the Work window first asks. Its stdout carries the protocol, one JSON object
// per line, passed to the window as `work://message`; its stderr carries the launcher's own
// messages (the wait for a model server that is still loading), passed as `work://stderr` for the
// start-up screen. What the window sends goes through `vetOutgoing` first.
//
// Spawned as the Codex app spawns its bundled agent: `<resources>/ling -c features.code_mode_host=true
// app-server`, `LOG_FORMAT=json`, `RUST_LOG=warn`, the resources folder on PATH (for a bundled
// `rg`); never the Codex app's analytics flag.

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import readline from "node:readline";

import { BusyTracker, busyMarker, classify, codexHome, findLing, servedModel, vetOutgoing } from "./bridge";
import type { ForView, Started } from "./api";

export interface ServerEvents {
  deliver(message: ForView): void;
  /** A turn started or ended somewhere: the power-save blocker and the busy marker follow it. */
  busyChanged(busy: boolean): void;
}

export class AppServer {
  private child: ChildProcessWithoutNullStreams | null = null;
  private readonly pending = new Set<string>();
  private busy = new BusyTracker();
  private marker: string | null = null;

  constructor(private readonly resourcesPath: string | null, private readonly events: ServerEvents) {}

  /** Starts `ling app-server` if it is not running. */
  start(): Started {
    const home = codexHome();
    if (this.child) return { served_model: home ? servedModel(home) : null, started: false };
    const ling = findLing(this.resourcesPath);
    if (!ling) throw new Error("ling is not installed: build it with `ling-admin codex build`, or install Mightling");
    const env: NodeJS.ProcessEnv = { ...process.env, LOG_FORMAT: "json", RUST_LOG: process.env.RUST_LOG ?? "warn" };
    if (this.resourcesPath) env.PATH = `${this.resourcesPath}${path.delimiter}${env.PATH ?? ""}`;
    const child = spawn(ling, ["-c", "features.code_mode_host=true", "app-server"], { env, stdio: ["pipe", "pipe", "pipe"] });
    this.child = child;
    this.pending.clear();
    this.busy = new BusyTracker();
    this.marker = home && child.pid ? busyMarker(home, child.pid) : null;
    readline.createInterface({ input: child.stdout }).on("line", (line) => this.incoming(line));
    readline.createInterface({ input: child.stderr }).on("line", (line) => this.events.deliver({ channel: "work://stderr", payload: line }));
    child.once("exit", (code) => this.exited(code));
    child.once("error", (error) => {
      this.events.deliver({ channel: "work://stderr", payload: `could not start ${ling} app-server: ${error.message}` });
      this.exited(null);
    });
    return { served_model: home ? servedModel(home) : null, started: true };
  }

  private incoming(line: string): void {
    if (line.trim() === "") return;
    const [kind, value] = classify(line);
    switch (kind.kind) {
      case "notProtocol":
        // Fails loudly: something wrote on the protocol channel.
        this.events.deliver({ channel: "work://protocol-error", payload: line });
        return;
      case "serverRequest":
        this.pending.add(kind.key);
        break;
      case "notification": {
        const params = (value as { params?: unknown }).params;
        if (this.busy.observe(kind.method, params)) {
          if (this.marker) {
            try {
              this.busy.syncMarker(this.marker);
            } catch {
              // The marker is a courtesy to Night Shift; the turn goes on without it.
            }
          }
          this.events.busyChanged(this.busy.isBusy);
        }
        break;
      }
      case "response":
        break;
    }
    this.events.deliver({ channel: "work://message", payload: value });
  }

  private exited(code: number | null): void {
    this.child = null;
    this.pending.clear();
    this.busy = new BusyTracker();
    if (this.marker) {
      try {
        this.busy.syncMarker(this.marker);
      } catch {
        // Nothing to remove.
      }
      this.marker = null;
    }
    this.events.busyChanged(false);
    this.events.deliver({ channel: "work://exit", payload: code });
  }

  /** Sends one protocol message: a request, the `initialized` notification, or an answer to a server request. */
  send(message: unknown): void {
    const vetted = vetOutgoing(message, this.pending);
    const child = this.child;
    if (!child || !child.stdin.writable) throw new Error("the agent's server is not running");
    child.stdin.write(`${JSON.stringify(vetted)}\n`);
  }

  /** Kills the server and removes its busy marker; the next `start` starts a fresh one. */
  stop(): void {
    const child = this.child;
    if (!child) return;
    child.removeAllListeners("exit");
    this.child = null;
    try {
      child.kill("SIGKILL");
    } catch {
      // Already gone.
    }
    this.exited(null);
  }

  get running(): boolean {
    return this.child !== null;
  }
}
