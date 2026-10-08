// The Ask window's server: `ling web` on this machine (specs/DREAMFERENCE_MIGHTLING_ASK.md §4, §10
// Phase B: "the app's Chat entry opens Ask"). The window loads the Mightling UI from it, so Ask
// threads get the policy layer that only `ling web` has: the `ask` prompt composed by the launcher,
// the scratch folder, the uploads. Nothing here speaks the protocol; the page does, over the
// server's own WebSocket.
//
// Signing in is `ling web`'s own: `ling web open --print-url` writes a one-time code, from this
// process, and the window trades it for a session cookie, as a browser does with `ling web open`.
// The app reads no credential file itself. When nothing answers on the port, the app starts
// `ling web serve` as its child (loopback only) and stops it on quit; a server the user runs as a
// unit (`ling web start`) is used as it is. No Electron here, so this tests on its own.

import { spawn, type ChildProcess } from "node:child_process";

/** Where `ling web` listens (ling-rs/web/src/server.rs `DEFAULT_PORT`). */
export const WEB_PORT = 3100;

/** The window's origin: loopback by address, as `ling web open` writes its links. */
export const WEB_ORIGIN = `http://127.0.0.1:${WEB_PORT}`;

/** How long a server this app started may take to answer. */
export const START_TIMEOUT_MS = 20_000;

export interface Ran {
  code: number | null;
  stdout: string;
  stderr: string;
}

/** Runs `ling <args>` to completion. */
export type Run = (args: string[]) => Promise<Ran>;

/** Starts `ling <args>` in the background. */
export type Start = (args: string[]) => ChildProcess;

export function runner(ling: string, env: NodeJS.ProcessEnv = process.env): { run: Run; start: Start } {
  return {
    run: (args) =>
      new Promise((resolve) => {
        const child = spawn(ling, args, { env, stdio: ["ignore", "pipe", "pipe"] });
        let stdout = "";
        let stderr = "";
        child.stdout.on("data", (chunk) => (stdout += String(chunk)));
        child.stderr.on("data", (chunk) => (stderr += String(chunk)));
        child.once("error", (error) => resolve({ code: null, stdout, stderr: stderr + error.message }));
        child.once("close", (code) => resolve({ code, stdout, stderr }));
      }),
    start: (args) => spawn(ling, args, { env, stdio: "ignore" }),
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** `ling web` for the Ask window: found running, or started and owned by this app. */
export class WebServer {
  private child: ChildProcess | null = null;

  /** The server reached through the `ling` at `ling`. */
  static for(ling: string): WebServer {
    const { run, start } = runner(ling);
    return new WebServer(run, start);
  }

  constructor(
    private readonly run: Run,
    private readonly start: Start,
    private readonly timeoutMs = START_TIMEOUT_MS,
    private readonly pollMs = 250,
  ) {}

  /** Whether a server answers its owner's health check (`ling web status` exits 0). */
  async running(): Promise<boolean> {
    return (await this.run(["web", "status"])).code === 0;
  }

  /** Makes sure a server answers, starting one if none does. */
  async ensure(): Promise<void> {
    if (await this.running()) return;
    if (!this.child || this.child.exitCode !== null) {
      const child = this.start(["web", "serve"]);
      child.once("exit", () => {
        if (this.child === child) this.child = null;
      });
      child.once("error", () => {
        if (this.child === child) this.child = null;
      });
      this.child = child;
    }
    const deadline = Date.now() + this.timeoutMs;
    while (Date.now() < deadline) {
      await sleep(this.pollMs);
      if (await this.running()) return;
      if (!this.child) break;
    }
    throw new Error("ling web did not start: `ling web serve` in a terminal shows why");
  }

  /** A one-time sign-in link for this machine, starting the server first when needed. */
  async loginUrl(): Promise<string> {
    await this.ensure();
    const answer = await this.run(["web", "open", "--print-url"]);
    const url = answer.stdout.trim().split(/\r?\n/).pop() ?? "";
    if (answer.code !== 0 || !isLoginUrl(url)) {
      throw new Error(answer.stderr.trim() || "ling web gave no sign-in link");
    }
    return url;
  }

  /** Stops the server if this app started it; one the user runs is left alone. */
  stop(): void {
    const child = this.child;
    this.child = null;
    if (child && child.exitCode === null) child.kill("SIGTERM");
  }

  /** Whether this app started the server that is running. */
  get owned(): boolean {
    return this.child !== null;
  }
}

/** A sign-in link to `ling web` on this machine, and nothing else, may be loaded. */
export function isLoginUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" && parsed.hostname === "127.0.0.1" && parsed.pathname === "/login" && /^[0-9a-f]{16,}$/.test(parsed.searchParams.get("code") ?? "");
  } catch {
    return false;
  }
}

/** Whether a URL is on the server the window was signed in to. */
export function sameServer(url: string, loginUrl: string): boolean {
  try {
    return new URL(url).origin === new URL(loginUrl).origin;
  } catch {
    return false;
  }
}
