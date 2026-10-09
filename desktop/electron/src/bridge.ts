// The app window's side of `ling app-server` (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §4.3), the
// pure half.
//
// The window speaks Codex's app-server protocol: one JSON object per line on the server's stdin
// and stdout, without JSON-RPC's `"jsonrpc"` field. Everything the window sends passes the policy
// first (policy.ts, the rules of `ling-rs/web/policy.json` that `ling web` enforces too), so a bug
// in the UI cannot reach a method that would replace Mightling's prompt, provider or policy, pair
// the machine with the upstream vendor, or upload anything. Everything the server writes passes
// `classify`, and `BusyTracker` keeps the marker Night Shift reads to know whether a turn is
// running (§8.3). No Electron here, so this tests on its own.

import fs from "node:fs";
import path from "node:path";

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
