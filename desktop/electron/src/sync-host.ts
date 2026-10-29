// The desktop app as a sync host (specs/DREAMFERENCE_MIGHTLING_SYNC.md §18.1, §18.2): the app's one
// app-server is this process's child on stdio, so only this process can follow a turn that runs in
// it and act on it for another device. The host posts to the hub (`POST /api/sync/host` on the
// node's `ling web`) what the server says about this device's threads (turns, items, deltas,
// approvals waiting), which threads it holds, its models and its projects, and carries out the
// commands the hub hands back (send, approve, stop, start) through `AppServer.send`, so the policy
// vets each one exactly as if a window had sent it, and says how each went.
//
// It runs only while the device is paired (`$CODEX_HOME/sync/device.toml`) and the sync agent has
// found the hub (`agent-status.json` names it); both are read again every 30 s, since the agent
// finds the node anew and an address is never kept here. Never in the egress audit's session
// (`MIGHTLING_APP_AUDIT`), which must connect to nothing. It talks to the hub and nothing else, with
// Node's own fetch: no proxy, no redirect. Threads that stay on this device stay here too: a
// throwaway thread (`ephemeral`), and a thread under a folder in `[sync] exclude` of the user's own
// `~/.config/dreamference/config.toml` (§17.3), whose events, holding and folder are never posted.
//
// Its own requests to the server carry ids no window uses (`sync␟<n>`); their answers stop at the
// observer hook (`AppServer.observe`) and never reach a window. No Electron here: sync-host.test.ts
// runs it against the real `AppServer` with a scripted `ling` and a stand-in hub on loopback.

import { randomBytes } from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { parse } from "smol-toml";

import { askRoot, canonicalRoot } from "./ask";
import { codexHome } from "./bridge";
import type { ServerObserver } from "./server";

/** The server's notifications a host passes on (`sync_live::FOLLOWED` on the hub). */
export const FOLLOWED: ReadonlySet<string> = new Set([
  "turn/started",
  "turn/completed",
  "item/started",
  "item/completed",
  "item/agentMessage/delta",
  "serverRequest/resolved",
  "thread/status/changed",
  "thread/name/updated",
  "error",
]);

/** The server requests another device may answer: the two approvals (`sync_live::ANSWERABLE`). */
export const ANSWERABLE: ReadonlySet<string> = new Set(["item/commandExecution/requestApproval", "item/fileChange/requestApproval"]);

/** The refusal of a command about a thread kept on this device (`[sync] exclude`, a throwaway), the same in `sync_live`'s host. */
const KEPT = "this thread is kept on this device";

/** The largest event posted whole (`sync_live::EVENT_CAP`); a bigger one goes as the stub the hub would keep. */
export const EVENT_CAP = 256 << 10;
/** The most event bytes in one post; the rest go in the next (the hub takes 16 MiB). */
const BATCH_CAP = 4 << 20;
/** Events waiting while the hub cannot be reached; the oldest go first. */
const QUEUE_CAP = 5000;
/** The prefix of this process's own request ids: windows' ids are `<window>␟<id>` with a number for a window. */
export const HOST_ID_PREFIX = "sync␟";
/** At most this many projects and models are offered (the hub keeps 50). */
const OFFERED = 50;

/** What the host needs of the app-server (`AppServer`). */
export interface HostedServer {
  send(message: unknown): Promise<void>;
  observe(observer: ServerObserver | null): void;
  readonly ready: boolean;
}

/** The pairing and the hub, as the agent left them. */
export interface HostConfig {
  device: string;
  secret: string;
  /** The hub's base URL (`http://192.168.50.105:3100`), from `agent-status.json`. */
  hub: string;
  /** Folders whose threads stay on this device, absolute. */
  exclude: string[];
}

export interface HostOptions {
  env?: NodeJS.ProcessEnv;
  /** A command was carried out: what the owner's screen says ("Approved from the phone"), and the thread. */
  told?: (text: string, thread: string | null) => void;
  /** Seconds an idle post waits at the hub for a command. */
  waitSeconds?: number;
  /** How long events gather before they are posted. */
  batchMs?: number;
  /** How often holding, models and projects are asked of the server. */
  refreshMs?: number;
  /** How often `device.toml` and `agent-status.json` are read again. */
  configMs?: number;
  /** The first wait after a failed post, doubled each time up to `backoffMaxMs`. */
  backoffMs?: number;
  backoffMaxMs?: number;
  /** How long the host waits for an answer to one of its own requests. */
  requestMs?: number;
  /** How often a host with no configuration or no ready server looks again. */
  idleMs?: number;
}

/** A command from the hub (`sync_live::Command`). */
interface Command {
  id: number;
  kind: string;
  thread?: string | null;
  text?: string | null;
  model?: string | null;
  request?: unknown;
  decision?: string | null;
  cwd?: string | null;
  by?: string;
}

interface Ack {
  id: number;
  ok: boolean;
  error?: string;
  thread?: string;
}

interface HostEvent {
  thread: string;
  message: unknown;
  bytes: number;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null);

/** Whether an address is the user's own network, as the agent judges it (`private_ip` in ling-rs/sync/src/cli.rs). */
export function privateIp(ip: string): boolean {
  const bare = ip.replace(/^\[|\]$/g, "");
  if (net.isIPv4(bare)) {
    const [a, b] = bare.split(".").map(Number);
    return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254) || (a === 100 && (b & 0xc0) === 64);
  }
  if (!net.isIPv6(bare)) return false;
  const lower = bare.toLowerCase();
  if (lower === "::1") return true;
  const mapped = lower.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  if (mapped) return privateIp(mapped[1]);
  const first = parseInt(lower.split(":")[0] || "0", 16);
  return (first & 0xfe00) === 0xfc00 || (first & 0xffc0) === 0xfe80;
}

/**
 * The hub's base URL from what the agent wrote, or null: plain `http` on the hub's own origin. An
 * address must be the user's own network; a name was resolved and checked by the agent.
 */
export function hubBase(value: unknown): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "http:" || url.username || url.password) return null;
  const host = url.hostname.replace(/^\[|\]$/g, "");
  if (net.isIP(host) && !privateIp(host)) return null;
  return url.origin;
}

/** A folder named in `[sync] exclude`: `~/x` or absolute; anything relative names no folder. */
function expand(folder: unknown, home: string | null): string | null {
  if (typeof folder !== "string") return null;
  const trimmed = folder.trim();
  if (trimmed === "~") return home;
  if (trimmed.startsWith("~/")) return home ? path.join(home, trimmed.slice(2)) : null;
  return path.isAbsolute(trimmed) ? trimmed : null;
}

const readToml = (file: string): Json | null => {
  try {
    const value = parse(fs.readFileSync(file, "utf8"));
    return isObject(value) ? value : null;
  } catch {
    return null;
  }
};

/** The host's configuration, or null when it must not run: not paired, no hub found yet, or the audit's session. */
export function readConfig(env: NodeJS.ProcessEnv = process.env): HostConfig | null {
  if (env.MIGHTLING_APP_AUDIT) return null;
  const home = codexHome(env);
  if (!home) return null;
  const device = readToml(path.join(home, "sync", "device.toml"));
  const id = text(device?.device);
  const secret = text(device?.secret);
  if (!id || !secret) return null;
  let status: unknown;
  try {
    status = JSON.parse(fs.readFileSync(path.join(home, "sync", "agent-status.json"), "utf8"));
  } catch {
    return null;
  }
  const hub = hubBase(isObject(status) ? status.hub : null);
  if (!hub) return null;
  const userHome = env.HOME || env.USERPROFILE || null;
  const user = userHome ? readToml(path.join(userHome, ".config", "dreamference", "config.toml")) : null;
  const sync = user && isObject(user.sync) ? user.sync : null;
  const exclude = sync && Array.isArray(sync.exclude) ? sync.exclude.map((folder) => expand(folder, userHome)).filter((folder): folder is string => folder !== null) : [];
  return { device: id, secret, hub, exclude };
}

/** Whether `folder` is `root` or inside it. */
function within(folder: string, root: string): boolean {
  const relative = path.relative(root, folder);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export class SyncHost {
  /** A random id per process: the hub tells this host from the device's agent by it. */
  readonly hostId = randomBytes(8).toString("hex");
  private readonly env: NodeJS.ProcessEnv;
  private readonly told: (text: string, thread: string | null) => void;
  private readonly waitSeconds: number;
  private readonly batchMs: number;
  private readonly refreshMs: number;
  private readonly configMs: number;
  private readonly backoffMs: number;
  private readonly backoffMaxMs: number;
  private readonly requestMs: number;
  private readonly idleMs: number;

  private running = false;
  private config: HostConfig | null = null;
  private configAt = 0;
  private refreshedAt = 0;
  private backoff = 0;
  private backoffUntil = 0;

  /** Threads loaded in the server, as last asked, and those it opened since. */
  private holding = new Set<string>();
  private models: string[] = [];
  private projects: string[] = [];
  /** The turn running in each thread. */
  private readonly turns = new Map<string, string>();
  /** The server's requests waiting for an answer, by id (as JSON): their method and thread. */
  private readonly asked = new Map<string, { method: string; thread: string | null }>();
  /** The folder each thread works in, as the server named it. */
  private readonly cwds = new Map<string, string>();
  /** Throwaway threads (a commit message's) and subagents: they stay here. */
  private readonly ephemeral = new Set<string>();

  private events: HostEvent[] = [];
  private acks: Ack[] = [];
  private next = 0;
  private readonly calls = new Map<string, { resolve: (result: unknown) => void; reject: (error: Error) => void; timer: NodeJS.Timeout }>();
  private readonly aborts = new Set<AbortController>();
  private readonly sleepers = new Set<{ timer: NodeJS.Timeout; resolve: () => void }>();
  private work: (() => void) | null = null;
  private chain: Promise<void> = Promise.resolve();

  constructor(private readonly server: HostedServer, options: HostOptions = {}) {
    this.env = options.env ?? process.env;
    this.told = options.told ?? (() => {});
    this.waitSeconds = options.waitSeconds ?? 25;
    this.batchMs = options.batchMs ?? 100;
    this.refreshMs = options.refreshMs ?? 10_000;
    this.configMs = options.configMs ?? 30_000;
    this.backoffMs = options.backoffMs ?? 5_000;
    this.backoffMaxMs = options.backoffMaxMs ?? 60_000;
    this.requestMs = options.requestMs ?? 30_000;
    this.idleMs = options.idleMs ?? 1_000;
  }

  /** Starts following the server and posting, once its app-server runs; again while running does nothing. */
  start(): void {
    if (this.running || this.env.MIGHTLING_APP_AUDIT) return;
    this.running = true;
    this.server.observe(this.observer);
    void this.pollLoop();
    void this.sendLoop();
    void this.refreshLoop();
  }

  /** Stops: the server went away or the app quits. Nothing in flight is waited for. */
  stop(): void {
    if (!this.running) return;
    this.running = false;
    this.server.observe(null);
    for (const controller of this.aborts) controller.abort();
    this.aborts.clear();
    for (const sleeper of this.sleepers) {
      clearTimeout(sleeper.timer);
      sleeper.resolve();
    }
    this.sleepers.clear();
    for (const call of this.calls.values()) {
      clearTimeout(call.timer);
      call.reject(new Error("the agent's server stopped"));
    }
    this.calls.clear();
    this.work?.();
    this.work = null;
    this.events = [];
    this.acks = [];
    this.holding = new Set();
    this.models = [];
    this.projects = [];
    this.turns.clear();
    this.asked.clear();
    this.cwds.clear();
    this.ephemeral.clear();
    this.config = null;
    this.configAt = 0;
    this.refreshedAt = 0;
    this.backoff = 0;
    this.backoffUntil = 0;
  }

  get active(): boolean {
    return this.running;
  }

  private sleep(ms: number): Promise<void> {
    if (!this.running) return Promise.resolve();
    return new Promise((resolve) => {
      const sleeper = { timer: setTimeout(() => {
        this.sleepers.delete(sleeper);
        resolve();
      }, Math.max(0, ms)), resolve };
      this.sleepers.add(sleeper);
    });
  }

  /** The configuration, read again when it is older than `configMs`. */
  private current(): HostConfig | null {
    const now = Date.now();
    if (this.configAt === 0 || now - this.configAt >= this.configMs) {
      this.config = readConfig(this.env);
      this.configAt = now;
      // Unpaired, or the hub lost: nothing waits to be posted to a hub that may never come.
      if (!this.config) {
        this.events = [];
        this.acks = [];
      }
    }
    return this.config;
  }

  /** Whether the host may talk now: configured, the server past its handshake, no back-off running. */
  private async ready(): Promise<HostConfig | null> {
    const config = this.current();
    if (!config || !this.server.ready) {
      await this.sleep(this.idleMs);
      return null;
    }
    const wait = this.backoffUntil - Date.now();
    if (wait > 0) {
      await this.sleep(wait);
      return null;
    }
    return config;
  }

  /** The idle half: a long post that waits at the hub for a command. */
  private async pollLoop(): Promise<void> {
    while (this.running) {
      const config = await this.ready();
      if (!config || !this.running) continue;
      if (this.refreshedAt === 0) await this.refresh();
      if (!this.running) return;
      await this.post(config, [], [], this.waitSeconds);
    }
  }

  /** The busy half: events and acks, gathered for `batchMs`, posted without waiting. */
  private async sendLoop(): Promise<void> {
    while (this.running) {
      if (this.events.length === 0 && this.acks.length === 0) {
        await new Promise<void>((resolve) => (this.work = resolve));
        this.work = null;
        continue;
      }
      await this.sleep(this.batchMs);
      const config = await this.ready();
      if (!config || !this.running) continue;
      const acks = this.acks.splice(0);
      let bytes = 0;
      let count = 0;
      while (count < this.events.length && (count === 0 || bytes + this.events[count].bytes <= BATCH_CAP)) bytes += this.events[count++].bytes;
      const events = this.events.splice(0, count);
      if (!(await this.post(config, events, acks, 0))) {
        // Kept for the next post, in order; the oldest events give way first.
        this.acks = [...acks, ...this.acks];
        this.events = [...events, ...this.events].slice(-QUEUE_CAP);
      }
    }
  }

  private async refreshLoop(): Promise<void> {
    while (this.running) {
      await this.sleep(this.refreshMs);
      if (this.running && this.current() && this.server.ready) await this.refresh();
    }
  }

  private wake(): void {
    this.work?.();
  }

  /** One post to the hub; true when the hub answered. Its commands are carried out in order. */
  private async post(config: HostConfig, events: HostEvent[], acks: Ack[], wait: number): Promise<boolean> {
    const body = JSON.stringify({
      host: this.hostId,
      kind: "desktop",
      holding: [...this.holding].filter((thread) => this.published(thread)),
      models: this.models,
      projects: this.projects,
      events: events.map(({ thread, message }) => ({ thread, message })),
      acks,
    });
    const controller = new AbortController();
    this.aborts.add(controller);
    const timer = setTimeout(() => controller.abort(), (wait + 15) * 1000);
    try {
      const response = await fetch(`${config.hub}/api/sync/host?wait=${wait}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${config.secret}`, Origin: config.hub, "Content-Type": "application/json" },
        body,
        signal: controller.signal,
        redirect: "error",
      });
      if (!response.ok) throw new Error(`the hub answered ${response.status}`);
      const answer = (await response.json()) as unknown;
      this.backoff = 0;
      this.backoffUntil = 0;
      const commands = isObject(answer) && Array.isArray(answer.commands) ? answer.commands : [];
      for (const command of commands) {
        if (isObject(command) && typeof command.id === "number" && typeof command.kind === "string") this.carryOut(command as unknown as Command);
      }
      return true;
    } catch {
      if (!this.running) return false;
      this.backoff = this.backoff === 0 ? this.backoffMs : Math.min(this.backoff * 2, this.backoffMaxMs);
      this.backoffUntil = Date.now() + this.backoff;
      return false;
    } finally {
      clearTimeout(timer);
      this.aborts.delete(controller);
    }
  }

  /** The back-off now, for the tests: 0 after an answer, then `backoffMs` doubling up to `backoffMaxMs`. */
  get currentBackoff(): number {
    return this.backoff;
  }

  /** A request of the host's own to the server, vetted by the policy like a window's. */
  private call(method: string, params: Json): Promise<Json> {
    const id = `${HOST_ID_PREFIX}${++this.next}`;
    return new Promise<Json>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.calls.delete(id);
        reject(new Error(`the agent's server did not answer ${method}`));
      }, this.requestMs);
      this.calls.set(id, { resolve: (result) => resolve(isObject(result) ? result : {}), reject, timer });
      this.server.send({ id, method, params }).catch((error: unknown) => {
        clearTimeout(timer);
        this.calls.delete(id);
        reject(error instanceof Error ? error : new Error(String(error)));
      });
    });
  }

  /** Holding, models and projects, asked of the server. */
  private async refresh(): Promise<void> {
    this.refreshedAt = Date.now();
    const [list, loaded, models] = await Promise.allSettled([
      this.call("thread/list", { limit: 50 }),
      this.call("thread/loaded/list", {}),
      this.call("model/list", {}),
    ]);
    if (!this.running) return;
    if (list.status === "fulfilled" && Array.isArray(list.value.data)) {
      const home = codexHome(this.env);
      let ask: string | null = null;
      if (home) {
        try {
          ask = canonicalRoot(askRoot(home));
        } catch {
          ask = askRoot(home);
        }
      }
      const projects: string[] = [];
      for (const thread of list.value.data) {
        if (!isObject(thread)) continue;
        this.note(thread);
        const cwd = text(thread.cwd);
        if (!cwd || (ask && within(cwd, ask)) || this.excluded(cwd) || projects.includes(cwd)) continue;
        if (projects.length < OFFERED) projects.push(cwd);
      }
      this.projects = projects;
    }
    if (loaded.status === "fulfilled" && Array.isArray(loaded.value.data)) {
      this.holding = new Set(loaded.value.data.filter((thread): thread is string => typeof thread === "string"));
    }
    if (models.status === "fulfilled" && Array.isArray(models.value.data)) {
      this.models = models.value.data
        .filter((model): model is Json => isObject(model) && model.hidden !== true && typeof model.id === "string")
        .map((model) => model.id as string)
        .slice(0, OFFERED);
    }
  }

  /** Takes note of a thread the server described: where it works, and whether it is a throwaway. */
  private note(thread: Json): void {
    const id = text(thread.id);
    if (!id) return;
    const cwd = text(thread.cwd);
    if (cwd) this.cwds.set(id, cwd);
    if (thread.ephemeral === true || typeof thread.parentThreadId === "string") this.ephemeral.add(id);
  }

  private excluded(cwd: string): boolean {
    return (this.config?.exclude ?? []).some((folder) => within(cwd, folder));
  }

  /** Whether a thread's live events leave this device: not a throwaway, and not under an excluded folder (unknown counts as excluded then). */
  private published(thread: string): boolean {
    if (this.ephemeral.has(thread)) return false;
    if ((this.config?.exclude ?? []).length === 0) return true;
    const cwd = this.cwds.get(thread);
    return cwd !== undefined && !this.excluded(cwd);
  }

  /** Whether a command must leave a thread alone: one not published. A thread not seen yet is read first (`thread/read` loads nothing). */
  private async kept(thread: string): Promise<boolean> {
    if (!this.cwds.has(thread) && !this.ephemeral.has(thread)) {
      const read = await this.call("thread/read", { threadId: thread }).catch((): Json => ({}));
      if (isObject(read.thread) && text(read.thread.id) === thread) this.note(read.thread);
    }
    return !this.published(thread);
  }

  private publish(thread: string, message: unknown): void {
    if (!this.config || !this.published(thread)) return;
    let json = JSON.stringify(message);
    if (json.length > EVENT_CAP) {
      // The stub the hub would keep: a huge command output is not carried to be thrown away there.
      const method = isObject(message) ? message.method : null;
      json = JSON.stringify({ method, params: { threadId: thread, truncated: json.length } });
    }
    this.events.push({ thread, message: JSON.parse(json) as unknown, bytes: json.length });
    if (this.events.length > QUEUE_CAP) this.events.splice(0, this.events.length - QUEUE_CAP);
    this.wake();
  }

  /** Every message from the server: the host's own answers end here; the rest is noted and, when followed, published. */
  private readonly observer: ServerObserver = (message, kind) => {
    if (!isObject(message)) return false;
    if (kind === "response") {
      if (typeof message.id === "string" && message.id.startsWith(HOST_ID_PREFIX)) {
        const call = this.calls.get(message.id);
        this.calls.delete(message.id);
        if (call) {
          clearTimeout(call.timer);
          if ("error" in message) {
            const error = isObject(message.error) && typeof message.error.message === "string" ? message.error.message : JSON.stringify(message.error);
            call.reject(new Error(error));
          } else {
            call.resolve(message.result);
          }
        }
        return true;
      }
      if (isObject(message.result) && isObject(message.result.thread)) this.note(message.result.thread);
      return false;
    }
    const method = typeof message.method === "string" ? message.method : "";
    const params = isObject(message.params) ? message.params : {};
    const thread = text(params.threadId);
    if (kind === "serverRequest") {
      this.asked.set(JSON.stringify(message.id), { method, thread });
      if (thread) this.publish(thread, message);
      return false;
    }
    switch (method) {
      case "thread/started":
        if (isObject(params.thread)) {
          this.note(params.thread);
          const id = text(params.thread.id);
          if (id) this.holding.add(id);
        }
        break;
      case "turn/started": {
        const turn = isObject(params.turn) ? text(params.turn.id) : null;
        if (thread && turn) this.turns.set(thread, turn);
        if (thread) this.holding.add(thread);
        break;
      }
      case "turn/completed":
        if (thread) this.turns.delete(thread);
        break;
      case "thread/closed":
        if (thread) {
          this.turns.delete(thread);
          this.holding.delete(thread);
        }
        break;
      case "serverRequest/resolved":
        if ("requestId" in params) this.asked.delete(JSON.stringify(params.requestId));
        break;
    }
    if (thread && FOLLOWED.has(method)) this.publish(thread, message);
    return false;
  };

  /** Carries a command out after the ones before it, then acks it. */
  private carryOut(command: Command): void {
    this.chain = this.chain.then(async () => {
      if (!this.running) return;
      const ack = await this.execute(command).catch((error: unknown): Ack => ({ id: command.id, ok: false, error: error instanceof Error ? error.message : String(error), ...(text(command.thread) ? { thread: text(command.thread) as string } : {}) }));
      if (!this.running) return;
      this.acks.push(ack);
      this.wake();
    });
  }

  private async execute(command: Command): Promise<Ack> {
    const by = text(command.by) ?? "another device";
    const thread = text(command.thread);
    const model = text(command.model);
    const refuse = (error: string): Ack => ({ id: command.id, ok: false, error, ...(thread ? { thread } : {}) });
    switch (command.kind) {
      case "send": {
        const message = typeof command.text === "string" ? command.text : "";
        if (!thread || message.trim() === "") return refuse("a message names its thread and has text");
        if (await this.kept(thread)) return refuse(KEPT);
        if (!this.holding.has(thread)) {
          await this.call("thread/resume", { threadId: thread });
          this.holding.add(thread);
        }
        const input = [{ type: "text", text: message, text_elements: [] }];
        const running = this.turns.get(thread);
        if (running !== undefined) await this.call("turn/steer", { threadId: thread, input, expectedTurnId: running });
        else await this.call("turn/start", { threadId: thread, input, ...(model ? { model } : {}) });
        this.told(`Message from ${by} sent`, thread);
        return { id: command.id, ok: true, thread };
      }
      case "approve": {
        const request = command.request;
        if (request === undefined || request === null) return refuse("an approval names the request it answers");
        const decision = command.decision;
        if (decision !== "accept" && decision !== "decline") return refuse("an approval is accept or decline");
        const key = JSON.stringify(request);
        const waiting = this.asked.get(key);
        // Only what this device asked and still waits is answered, about the thread it asked in (never
        // the hub's word for it); the policy then checks the answer again, as it does a window's.
        if (!waiting) return refuse("that approval is not waiting any more");
        if (!ANSWERABLE.has(waiting.method)) return refuse(`${waiting.method} is answered on this device`);
        const about = waiting.thread;
        const ack = (error: string): Ack => ({ id: command.id, ok: false, error, ...(about ? { thread: about } : {}) });
        if (!about || (await this.kept(about))) return ack(KEPT);
        await this.server.send({ id: request, result: { decision } });
        this.asked.delete(key);
        this.told(decision === "accept" ? `Approved from ${by}` : `Declined from ${by}`, about);
        return { id: command.id, ok: true, thread: about };
      }
      case "stop": {
        if (!thread) return refuse("a stop names its thread");
        if (await this.kept(thread)) return refuse(KEPT);
        const running = this.turns.get(thread);
        if (running === undefined) return refuse("no turn is running in this thread");
        await this.call("turn/interrupt", { threadId: thread, turnId: running });
        this.told(`Stopped from ${by}`, thread);
        return { id: command.id, ok: true, thread };
      }
      case "start": {
        const cwd = text(command.cwd);
        if (!cwd || !this.projects.includes(cwd)) return refuse("a new thread starts in one of the folders this device offers");
        const started = await this.call("thread/start", { cwd, ...(model ? { model } : {}) });
        const opened = isObject(started.thread) ? text(started.thread.id) : null;
        if (!opened) return refuse("the agent's server named no thread");
        this.holding.add(opened);
        this.cwds.set(opened, cwd);
        const message = typeof command.text === "string" ? command.text : "";
        if (message.trim() !== "") {
          try {
            await this.call("turn/start", { threadId: opened, input: [{ type: "text", text: message, text_elements: [] }], ...(model ? { model } : {}) });
          } catch (error) {
            return { id: command.id, ok: false, error: error instanceof Error ? error.message : String(error), thread: opened };
          }
        }
        this.told(`New thread from ${by}`, opened);
        return { id: command.id, ok: true, thread: opened };
      }
      default:
        return refuse(`this device does not carry out ${command.kind}`);
    }
  }
}
