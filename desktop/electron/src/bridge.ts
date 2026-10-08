// The Work window's side of `ling app-server` (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §4.3), the
// pure half: a port of the Rust crate `ling-desktop-bridge`, with the same guarantees.
//
// The window speaks Codex's app-server protocol: one JSON object per line on the server's stdin
// and stdout, without JSON-RPC's `"jsonrpc"` field. Everything the window sends passes
// `vetOutgoing` first, so a bug in the UI cannot reach a method that would replace Mightling's
// prompt, provider or policy, pair the machine with the upstream vendor, or upload anything.
// Everything the server writes passes `classify`, and `BusyTracker` keeps the marker Night Shift
// reads to know whether a turn is running (§8.3). No Electron here, so this tests on its own.

import fs from "node:fs";
import path from "node:path";

/** Client requests the window may send. Everything else is dropped by `vetOutgoing`: notably
 * `feedback/upload`, `account/login/*`, `account/bedrock/*`, `remoteControl/*`,
 * `thread/realtime/*` and `userVerification/*` (§4.3). */
export const ALLOWED_REQUESTS: readonly string[] = [
  "initialize",
  "account/read",
  "config/read",
  "config/value/write",
  "config/batchWrite",
  "configRequirements/read",
  "model/list",
  "permissionProfile/list",
  "collaborationMode/list",
  "project/list",
  "project/read",
  "project/create",
  "project/update",
  "thread/start",
  "thread/resume",
  "thread/fork",
  "thread/list",
  "thread/read",
  "thread/loaded/list",
  "thread/turns/list",
  "thread/items/list",
  "thread/search",
  "thread/name/set",
  "thread/metadata/update",
  "thread/archive",
  "thread/unarchive",
  "thread/unsubscribe",
  "thread/compact/start",
  "thread/revert",
  "thread/queue/add",
  "thread/queue/list",
  "thread/queue/update",
  "thread/queue/delete",
  "thread/queue/reorder",
  "thread/queue/start",
  "turn/start",
  "turn/steer",
  "turn/interrupt",
  "fuzzyFileSearch",
  "gitDiffToRemote",
  "skills/list",
  "mcpServerStatus/list",
];

/** Client notifications the window may send. */
export const ALLOWED_NOTIFICATIONS: readonly string[] = ["initialized"];

/** Methods that open, resume or fork a thread, whose parameters lose `DROPPED_THREAD_FIELDS`. */
const THREAD_OPENERS: readonly string[] = ["thread/start", "thread/resume", "thread/fork"];

/** Thread parameters the window never sends: each would replace what the launcher set up, the
 * prompt (`baseInstructions`, `developerInstructions`), the provider (`modelProvider`), the policy
 * (`config`), or select a style cave mode owns (`personality`). */
export const DROPPED_THREAD_FIELDS: readonly string[] = [
  "baseInstructions", "developerInstructions", "modelProvider", "config", "personality",
];

type JsonObject = Record<string, unknown>;

/** One thing the server wrote on stdout. */
export type Incoming =
  | { kind: "response" }
  | { kind: "serverRequest"; key: string; method: string }
  | { kind: "notification"; method: string }
  /** A line that is not a protocol message: something wrote where it must not. */
  | { kind: "notProtocol" };

const isObject = (value: unknown): value is JsonObject => typeof value === "object" && value !== null && !Array.isArray(value);

/** Reads a line the server wrote. */
export function classify(line: string): [Incoming, unknown] {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    return [{ kind: "notProtocol" }, undefined];
  }
  if (!isObject(value)) return [{ kind: "notProtocol" }, undefined];
  const method = typeof value.method === "string" ? value.method : undefined;
  const hasId = "id" in value;
  if (method !== undefined && hasId) return [{ kind: "serverRequest", key: JSON.stringify(value.id), method }, value];
  if (method !== undefined) return [{ kind: "notification", method }, value];
  if (hasId && ("result" in value || "error" in value)) return [{ kind: "response" }, value];
  return [{ kind: "notProtocol" }, value];
}

/**
 * Checks a message the window wants to send, removes what must never be sent, and returns it.
 * `pending` holds the ids (as JSON) of server requests not yet answered; answering one removes it,
 * and an answer to anything else is refused, so the window cannot invent approvals.
 */
export function vetOutgoing(message: unknown, pending: Set<string>): JsonObject {
  if (!isObject(message)) throw new Error("a message must be a JSON object");
  const object: JsonObject = { ...message };
  delete object.jsonrpc;
  const method = typeof object.method === "string" ? object.method : undefined;
  const key = "id" in object ? JSON.stringify(object.id) : undefined;
  if (method !== undefined && key !== undefined) {
    if (!ALLOWED_REQUESTS.includes(method)) throw new Error(`the Work window does not send ${method}`);
    if (THREAD_OPENERS.includes(method) && isObject(object.params)) {
      const params = { ...object.params };
      for (const field of DROPPED_THREAD_FIELDS) delete params[field];
      object.params = params;
    }
    return object;
  }
  if (method !== undefined) {
    if (ALLOWED_NOTIFICATIONS.includes(method)) return object;
    throw new Error(`the Work window does not send the notification ${method}`);
  }
  if (key !== undefined && ("result" in object || "error" in object)) {
    if (!pending.delete(key)) throw new Error(`no server request ${key} is waiting for an answer`);
    return object;
  }
  throw new Error("not a request, a notification or an answer");
}

/** The threads with a turn running, from the server's own notifications, and the marker that tells Night Shift about them (§8.3). */
export class BusyTracker {
  private readonly threads = new Set<string>();

  /** Notes a notification. Returns true when the set of busy threads changed. */
  observe(method: string, params: unknown): boolean {
    const thread = isObject(params) && typeof params.threadId === "string" ? params.threadId : null;
    if (thread === null) return false;
    switch (method) {
      case "turn/started":
        if (this.threads.has(thread)) return false;
        this.threads.add(thread);
        return true;
      case "turn/completed":
      case "thread/closed":
        return this.threads.delete(thread);
      default:
        return false;
    }
  }

  get isBusy(): boolean {
    return this.threads.size > 0;
  }

  /** The marker's contents: the busy threads, for a person reading the folder. */
  markerText(): string {
    return JSON.stringify({ threads: [...this.threads].sort() });
  }

  /** Writes the marker while a turn runs and removes it when none does. */
  syncMarker(marker: string): void {
    if (this.isBusy) {
      fs.mkdirSync(path.dirname(marker), { recursive: true });
      fs.writeFileSync(marker, this.markerText());
    } else {
      fs.rmSync(marker, { force: true });
    }
  }
}

/** `ling`'s home folder: `$CODEX_HOME`, else `~/.mightling` (`ling-rs/src/home.rs`). */
export function codexHome(env: NodeJS.ProcessEnv = process.env): string | null {
  if (env.CODEX_HOME) return env.CODEX_HOME;
  return env.HOME ? path.join(env.HOME, ".mightling") : null;
}

/** Where the busy marker of the app-server with process id `pid` goes; Night Shift reads the folder (`NightShiftHost.busy_app_server_pids`). */
export function busyMarker(home: string, pid: number): string {
  return path.join(home, "night", "busy", String(pid));
}

/** The model the launcher's catalog names (`$CODEX_HOME/model_catalog.json`), which the window passes in every `thread/start` (§5). */
export function servedModel(home: string): string | null {
  try {
    const catalog = JSON.parse(fs.readFileSync(path.join(home, "model_catalog.json"), "utf8"));
    const first = catalog?.models?.[0];
    const name = first?.slug ?? first?.id;
    return typeof name === "string" ? name : null;
  } catch {
    return null;
  }
}

const isFile = (file: string) => {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
};

/**
 * The `ling` executable: `$MIGHTLING_BIN`, then the one bundled beside the app (`resourcesPath`,
 * then the unpacked asar), then `ling` on PATH, then `~/.local/bin/ling`. Never a bare `codex`: the
 * launcher is what brings Mightling's model server, prompt and home (§4.3).
 */
export function findLing(resourcesPath: string | null, env: NodeJS.ProcessEnv = process.env): string | null {
  if (env.MIGHTLING_BIN && isFile(env.MIGHTLING_BIN)) return env.MIGHTLING_BIN;
  if (resourcesPath) {
    for (const candidate of [path.join(resourcesPath, "ling"), path.join(resourcesPath, "app.asar.unpacked", "ling")]) {
      if (isFile(candidate)) return candidate;
    }
  }
  for (const dir of (env.PATH ?? "").split(path.delimiter).filter(Boolean)) {
    const candidate = path.join(dir, "ling");
    if (isFile(candidate)) return candidate;
  }
  const local = env.HOME ? path.join(env.HOME, ".local", "bin", "ling") : null;
  return local && isFile(local) ? local : null;
}
