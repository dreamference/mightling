// The app window's end of the main process (desktop/electron/src/main.ts): one object the
// preload exposes, `window.electronBridge.sendMessageFromView(message)`, and window `MessageEvent`s
// for what the main process sends — the Codex app's shape, so this page could also run in a
// browser against another transport. Nothing else in the UI touches Electron.

import type { Transport } from "./rpc";

export interface Started {
  served_model: string | null;
  started: boolean;
  /** The folder Ask threads' scratch folders live in (`$CODEX_HOME/ask`), when the host knows it. */
  ask_root?: string | null;
}

/** Which host the page runs in: `ling web` in a browser, or the desktop app's window (`app://`). */
export type Host = "web" | "electron";

export function host(): Host {
  return typeof window !== "undefined" && window.mightlingWindowType === "electron" ? "electron" : "web";
}

export interface WorkTarget {
  cwd: string | null;
  thread: string | null;
}

export interface Airgapped {
  level: "off" | "on";
  source: string;
}

/** What the preload exposes (desktop/electron/src/preload.ts). */
interface ElectronBridge {
  sendMessageFromView(message: unknown): Promise<unknown>;
  /** Where a dropped file lives on disk; "" when it has no path (older preloads lack it). */
  pathForFile?(file: File): string;
}

declare global {
  interface Window {
    electronBridge?: ElectronBridge;
    mightlingWindowType?: string;
  }
}

function send<T>(message: object): Promise<T> {
  const bridge = window.electronBridge;
  if (!bridge) return Promise.reject(new Error("this page is not inside Mightling's desktop app"));
  return bridge.sendMessageFromView(message) as Promise<T>;
}

export const bridgeTransport: Transport = {
  send: (message: object) => send<void>({ type: "work/send", message }),
};

export const startServer = () => send<Started>({ type: "work/start" });
export const stopServer = () => send<void>({ type: "work/stop" });
export const workTarget = () => send<WorkTarget>({ type: "work/target" });
export const airgapped = (thread: string | null) => send<Airgapped>({ type: "work/airgapped", thread });
/** `ling airgapped default <level>`: the level new threads start at; resolves with what it printed. */
export const airgappedDefault = (level: "off" | "on") => send<string>({ type: "work/airgapped-default", level });
export const contextMenu = (x: number, y: number, editable: boolean, selection: string) =>
  send<void>({ type: "context-menu", x, y, editable, selection });
/** One file in `git status`: index and work-tree letters ("." none, "?" untracked). */
export interface GitFile {
  path: string;
  staged: string;
  unstaged: string;
  from: string | null;
}

export interface GitStatus {
  root: string;
  branch: string | null;
  head: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  files: GitFile[];
}

export type GitScope = "unstaged" | "staged" | "commit" | "branch";

export interface GitCommit {
  sha: string;
  subject: string;
  author: string;
  time: number;
}

export interface Editor {
  id: string;
  name: string;
  command: string;
}

/**
 * Window-side git (desktop/electron/src/git.ts): only the desktop app has it. A browser on `ling
 * web` shows the review pane's Last turn scope, which comes from the server.
 */
export const git = {
  available: () => host() === "electron",
  repos: (cwd: string) => send<string[]>({ type: "git/repos", cwd }),
  status: (root: string) => send<GitStatus>({ type: "git/status", root }),
  diff: (root: string, scope: GitScope, commit: string | null = null) => send<{ scope: GitScope; diff: string; against: string | null }>({ type: "git/diff", root, scope, commit }),
  log: (root: string) => send<GitCommit[]>({ type: "git/log", root }),
  stage: (root: string, paths: string[]) => send<void>({ type: "git/stage", root, paths }),
  unstage: (root: string, paths: string[]) => send<void>({ type: "git/unstage", root, paths }),
  discard: (root: string, paths: string[]) => send<void>({ type: "git/discard", root, paths }),
  apply: (root: string, patch: string, options: { cached?: boolean; reverse?: boolean; index?: boolean }) => send<void>({ type: "git/apply", root, patch, ...options }),
  commit: (root: string, message: string, all: boolean) => send<string>({ type: "git/commit", root, message, all }),
  push: (root: string, force: boolean, thread: string | null) => send<string>({ type: "git/push", root, force, thread }),
  pullRequest: (root: string, thread: string | null) => send<string>({ type: "git/pull-request", root, thread }),
};

export const listEditors = () => send<Editor[]>({ type: "editors/list" });
/** Opens a file of `root` in `editor` (null: the system's choice), at `line` when given. */
export const openInEditor = (root: string, path: string, line: number | null, editor: string | null) =>
  send<null>({ type: "editor/open", root, path, line, editor });

/** The window's own preferences (desktop/electron/src/prefs.ts), whole. */
export const readPrefs = () => send<Record<string, unknown>>({ type: "prefs/read" });
/** Writes some preferences; resolves with all of them. */
export const writePrefs = (values: Record<string, unknown>) => send<Record<string, unknown>>({ type: "prefs/write", values });

/** A dropped file's path on disk, in the app; null in a browser, which never learns one. */
export function pathForFile(file: File): string | null {
  const path = window.electronBridge?.pathForFile?.(file) ?? "";
  return path ? path : null;
}

export const windowControl = (action: "minimize" | "maximize" | "close") => send<void>({ type: `window/${action}` });

export interface Uploaded {
  path: string;
  bytes: number;
}

/** A file's bytes as base64, built in pieces so a large file does not overflow the argument list. */
export async function base64(file: Blob): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

/**
 * Writes an attachment into an Ask thread's scratch folder, and resolves with where it landed: in
 * a browser through `ling web`'s `/api/upload` (specs/DREAMFERENCE_MIGHTLING_ASK.md §4.1), in the
 * app through its main process, which applies the same checks (desktop/electron/src/ask.ts).
 */
export async function upload(thread: string, file: Blob & { name?: string }, kind: "image" | "file", name?: string): Promise<Uploaded> {
  const fileName = name ?? file.name ?? "attachment";
  if (host() === "electron") {
    return send<Uploaded>({ type: "ask/upload", thread, name: fileName, kind, data: await base64(file) });
  }
  const query = new URLSearchParams({ thread, name: fileName, kind });
  const response = await fetch(`/api/upload?${query.toString()}`, { method: "POST", body: file, credentials: "same-origin" });
  if (!response.ok) throw new Error((await response.text()).trim() || `the upload failed (${response.status})`);
  return (await response.json()) as Uploaded;
}

export interface BridgeEvents {
  message: (message: unknown) => void;
  stderr: (line: string) => void;
  protocolError: (line: string) => void;
  exit: (code: number | null) => void;
  /** The app asked for Ask or Work (its menu, a second instance, a link). */
  view?: (view: "ask" | "work") => void;
}

/** Subscribes to the bridge's events; resolves with a function that unsubscribes them all. */
export async function listenBridge(events: BridgeEvents): Promise<() => void> {
  const onMessage = (event: MessageEvent) => {
    const data = event.data as { channel?: string; payload?: unknown } | null;
    if (!data || typeof data !== "object" || typeof data.channel !== "string") return;
    switch (data.channel) {
      case "work://message":
        return events.message(data.payload);
      case "work://stderr":
        return events.stderr(String(data.payload));
      case "work://protocol-error":
        return events.protocolError(String(data.payload));
      case "work://exit":
        return events.exit(typeof data.payload === "number" ? data.payload : null);
      case "view":
        return events.view?.(data.payload === "ask" ? "ask" : "work");
    }
  };
  window.addEventListener("message", onMessage);
  return () => window.removeEventListener("message", onMessage);
}
