// The connection to `ling app-server` (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §4.3), the process
// half of the bridge. One server per app, started through the launcher (`ling app-server`, never a
// bare `codex`) when the app window first asks. Its stdout carries the protocol, one JSON object
// per line, passed to the window as `work://message`; its stderr carries the launcher's own
// messages (the wait for a model server that is still loading), passed as `work://stderr` for the
// start-up screen. What the window sends goes through the policy first (policy.ts), which is also
// what makes Ask threads possible here: the `ask` prompt composed by the launcher, a scratch folder
// under `$CODEX_HOME/ask`, and attachments written into it (ask.ts;
// specs/DREAMFERENCE_MIGHTLING_ASK.md §3).
//
// Spawned as the Codex app spawns its bundled agent: `<resources>/ling -c features.code_mode_host=true
// app-server`, `LOG_FORMAT=json`, `RUST_LOG=warn`, the resources folder on PATH (for a bundled
// `rg`); never the Codex app's analytics flag.

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import readline from "node:readline";

import { MessagePrompts, askRoot, canonicalRoot, composePrompt, isFolder, linkThread, writeUpload, type Upload } from "./ask";
import { BusyTracker, busyMarker, classify, codexHome, findLing, servedModel } from "./bridge";
import { POLICY, namedPrompt, vetOutgoing } from "./policy";
import type { ForView, Started } from "./api";

export interface ServerEvents {
  deliver(message: ForView): void;
  /** A turn started or ended somewhere: the power-save blocker and the busy marker follow it. */
  busyChanged(busy: boolean): void;
}

export class AppServer {
  private child: ChildProcessWithoutNullStreams | null = null;
  private readonly pending = new Set<string>();
  /** Thread openers whose new thread works in an Ask folder, by request id: answered, the folder is named after the thread. */
  private readonly openers = new Map<string, string>();
  /** How the server was started: the same `ling` and environment compose the named prompts. */
  private launch: { ling: string; env: NodeJS.ProcessEnv } | null = null;
  private busy = new BusyTracker();
  private marker: string | null = null;

  constructor(private readonly resourcesPath: string | null, private readonly events: ServerEvents) {}

  /** Starts `ling app-server` if it is not running. */
  start(): Started {
    const home = codexHome();
    if (this.child) return { served_model: home ? servedModel(home) : null, started: false, ask_root: this.askRootFor(home) };
    const ling = findLing(this.resourcesPath);
    if (!ling) throw new Error("ling is not installed: build it with `ling-admin codex build`, or install Mightling");
    const env: NodeJS.ProcessEnv = { ...process.env, LOG_FORMAT: "json", RUST_LOG: process.env.RUST_LOG ?? "warn" };
    if (this.resourcesPath) env.PATH = `${this.resourcesPath}${path.delimiter}${env.PATH ?? ""}`;
    const child = spawn(ling, ["-c", "features.code_mode_host=true", "app-server"], { env, stdio: ["pipe", "pipe", "pipe"] });
    this.child = child;
    this.launch = { ling, env };
    this.pending.clear();
    this.openers.clear();
    this.busy = new BusyTracker();
    this.marker = home && child.pid ? busyMarker(home, child.pid) : null;
    readline.createInterface({ input: child.stdout }).on("line", (line) => this.incoming(line));
    readline.createInterface({ input: child.stderr }).on("line", (line) => this.events.deliver({ channel: "work://stderr", payload: line }));
    child.once("exit", (code) => this.exited(code));
    child.once("error", (error) => {
      this.events.deliver({ channel: "work://stderr", payload: `could not start ${ling} app-server: ${error.message}` });
      this.exited(null);
    });
    return { served_model: home ? servedModel(home) : null, started: true, ask_root: this.askRootFor(home) };
  }

  /** Where Ask threads' folders are, as the server will name their `cwd` (canonical), so the page can tell Ask threads from Work's projects. */
  private askRootFor(home: string | null): string | null {
    if (!home) return null;
    try {
      return canonicalRoot(askRoot(home));
    } catch {
      return null;
    }
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
      case "response": {
        // A thread that works in an Ask folder: once the server names it, so is the folder.
        const key = JSON.stringify((value as { id?: unknown }).id);
        const folder = this.openers.get(key);
        this.openers.delete(key);
        const thread = (value as { result?: { thread?: { id?: unknown } } }).result?.thread?.id;
        const home = codexHome();
        if (folder && home && typeof thread === "string") {
          try {
            linkThread(askRoot(home), thread, folder);
          } catch {
            // The thread still works in its folder; only resuming it there later needs the link.
          }
        }
        break;
      }
    }
    this.events.deliver({ channel: "work://message", payload: value });
  }

  private exited(code: number | null): void {
    this.child = null;
    this.pending.clear();
    this.openers.clear();
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

  /**
   * Sends one protocol message: a request, the `initialized` notification, or an answer to a
   * server request. A named prompt's text is read before vetting, since composing runs `ling`.
   */
  async send(message: unknown): Promise<void> {
    const home = codexHome();
    const launch = this.launch;
    if (!this.child || !launch) throw new Error("the agent's server is not running");
    // Without a home folder there is no Ask root: a named prompt is refused, Work goes on.
    const root = home ? askRoot(home) : "";
    const name = namedPrompt(message);
    if (name && !home) throw new Error("ling's home folder is unknown: HOME is not set");
    // The app-server's environment without its log settings, which are the server's alone: the
    // composed text is stdout, whole.
    const { LOG_FORMAT: _format, RUST_LOG: _level, ...composeEnv } = launch.env;
    const prompt = name ? { name, text: await composePrompt(launch.ling, name, root, composeEnv) } : null;
    const prompts = new MessagePrompts(root, prompt);
    const vetted = vetOutgoing(message, this.pending, prompts);
    const child = this.child;
    if (!child || !child.stdin.writable) throw new Error("the agent's server is not running");
    const method = typeof vetted.method === "string" ? vetted.method : null;
    const cwd = (vetted.params as { cwd?: unknown } | undefined)?.cwd;
    if (method && POLICY.threadOpeners.has(method) && "id" in vetted && typeof cwd === "string" && isFolder(root, cwd)) {
      this.openers.set(JSON.stringify(vetted.id), cwd);
    }
    child.stdin.write(`${JSON.stringify(vetted)}\n`);
  }

  /** Writes an attachment into an Ask thread's folder; answers where it landed. */
  upload(upload: Upload): { path: string; bytes: number } {
    const home = codexHome();
    if (!home) throw new Error("ling's home folder is unknown: HOME is not set");
    return writeUpload(askRoot(home), upload);
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
