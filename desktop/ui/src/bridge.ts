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

/** What a link names (desktop/electron/src/args.ts). */
export interface LinkTarget extends WorkTarget {
  prompt?: string | null;
  page?: "new" | "settings" | "skills" | "night" | "plugins" | "pets" | "ask" | "quick";
  plugin?: string;
  /** With `page: "ask"`: send the prompt as a new question at once (Quick Entry). */
  send?: boolean;
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
  /** Why git cannot run on this machine (a Mac without the Command Line Tools), or null. */
  missing: () => send<string | null>({ type: "git/missing" }),
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

export interface ManagedWorktree {
  root: string;
  cwd: string;
  sourceRoot: string;
  sourceCwd: string;
  headSha: string;
  branch: string | null;
  included?: string[];
  owner?: string | null;
  modified?: number;
}

/** Managed worktrees and hand-off (desktop/electron/src/worktree.ts); `root` is the configured worktree root, null for the default. */
export const worktrees = {
  create: (sourceCwd: string, base: string | null, withChanges: boolean, root: string | null) => send<ManagedWorktree>({ type: "worktree/create", sourceCwd, base, withChanges, root }),
  bind: (checkout: string, thread: string) => send<void>({ type: "worktree/bind", checkout, thread }),
  list: (sourceCwd: string, root: string | null) => send<ManagedWorktree[]>({ type: "worktree/list", sourceCwd, root }),
  remove: (sourceCwd: string, checkout: string, root: string | null, snapshot: string | null) => send<string | null>({ type: "worktree/remove", sourceCwd, checkout, root, snapshot }),
  restore: (sourceCwd: string, snapshot: string, root: string | null) => send<ManagedWorktree>({ type: "worktree/restore", sourceCwd, snapshot, root }),
  branch: (checkout: string, name: string) => send<string>({ type: "worktree/branch", checkout, name }),
  handOff: (direction: "toLocal" | "toWorktree", worktree: string, local: string, root: string | null, thread: string) =>
    send<{ snapshot: string | null }>({ type: "worktree/handoff", direction, worktree, local, root, thread }),
  branches: (root: string) => send<{ current: string | null; branches: string[] }>({ type: "git/branches", root }),
};

export interface EnvironmentAction {
  name: string;
  icon: string;
  script: string;
}

export interface LocalEnvironment {
  file: string;
  folder: string;
  name: string;
  setup: string;
  actions: EnvironmentAction[];
}

/** A project's local environments, read from .codex/ and .mightling/; written to .mightling/ only. */
export const localEnvironments = (project: string) => send<{ found: LocalEnvironment[]; problems: string[] }>({ type: "env/list", project });
export const writeEnvironment = (project: string, environment: { name: string; setup: string; actions: EnvironmentAction[] }) =>
  send<string>({ type: "env/write", project, environment });

/**
 * Runs a project environment's setup or action in the bottom panel, in `cwd` (the project or one of
 * its worktrees); the main process reads the script from the project. Output arrives as task events.
 */
export const runTask = (project: string, file: string, action: number | "setup", cwd: string, thread: string | null) =>
  send<string>({ type: "task/run", project, file, action, cwd, thread });
export const stopTask = (id: string) => send<null>({ type: "task/stop", id });
export const openTerminal = (dir: string) => send<null>({ type: "open/terminal", dir });
export const openFolder = (dir: string) => send<null>({ type: "open/folder", dir });

/**
 * Opens a document the local file index cited (`docs:documents/42`): in the app with the system's
 * handler, through the main process, which asks `ling docs locate` for the file inside its
 * collection; in a browser from `ling web`'s `/docs/` route, in a new tab (a PDF at its page).
 */
export function openDocument(doc: string, page: number | null): Promise<null> {
  if (!/^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}\/\d{1,18}$/.test(doc)) return Promise.reject(new Error("not a document"));
  if (host() === "electron") return send<null>({ type: "docs/open", doc });
  window.open(`/docs/${doc}${page === null ? "" : `#page=${page}`}`, "_blank", "noopener");
  return Promise.resolve(null);
}
export const openEditorAt = (dir: string, editor: string | null) => send<null>({ type: "open/editor", dir, editor });

export interface TaskEvent {
  id: string;
  kind: "started" | "output" | "exit";
  label?: string;
  cwd?: string;
  data?: string;
  code?: number | null;
}

export const listEditors = () => send<Editor[]>({ type: "editors/list" });
/** Opens a file of `root` in `editor` (null: the system's choice), at `line` when given. */
export const openInEditor = (root: string, path: string, line: number | null, editor: string | null) =>
  send<null>({ type: "editor/open", root, path, line, editor });

/** A file's size in bytes, asked of the app before a file tab reads it whole; null in a browser or for no such file. */
export async function fileSize(path: string): Promise<number | null> {
  if (host() !== "electron") return null;
  return send<number | null>({ type: "file/size", path });
}

/** The window's own preferences (desktop/electron/src/prefs.ts), whole. */
export const readPrefs = () => send<Record<string, unknown>>({ type: "prefs/read" });
/** Writes some preferences; resolves with all of them. */
export const writePrefs = (values: Record<string, unknown>) => send<Record<string, unknown>>({ type: "prefs/write", values });
/** Quick Entry's shortcut as the main process holds it (DESKTOP_PARITY_CLAUDE §4.2); the desktop app's only. */
export interface QuickStatus {
  on: boolean;
  shortcut: string;
  registered: boolean;
  problem: string | null;
}
export const quickStatus = () => send<QuickStatus>({ type: "quick/status" });

/** A dropped file's path on disk, in the app; null in a browser, which never learns one. */
export function pathForFile(file: File): string | null {
  const path = window.electronBridge?.pathForFile?.(file) ?? "";
  return path ? path : null;
}

export const windowControl = (action: "minimize" | "maximize" | "close") => send<void>({ type: `window/${action}` });
/** F11: the app's window full screen; in a browser, the page. */
export async function toggleFullScreen(): Promise<void> {
  if (host() === "electron") return send<void>({ type: "window/fullscreen" });
  if (document.fullscreenElement) await document.exitFullscreen();
  else await document.documentElement.requestFullscreen();
}
/** Ctrl+Q: quits the app (a browser tab is the browser's to close). */
export const quitApp = () => (host() === "electron" ? send<void>({ type: "app/quit" }) : Promise.resolve());
/** Quits and starts the app again: its agent's server resolves the node as it starts (Settings › Node). */
export const relaunchApp = () => send<void>({ type: "app/relaunch" });
/** A thread in a window of its own (the app only); a browser opens the thread in a new tab. */
export async function popOut(thread: string): Promise<void> {
  if (host() === "electron") return send<void>({ type: "window/popout", thread });
  window.open(`${location.pathname}#work`, "_blank");
}
/** Keeps this window above the others; resolves with whether it now is. */
export const alwaysOnTop = (on: boolean) => send<boolean>({ type: "window/always-on-top", on });
/** The user's home folder, where a thread with no project works. */
export const homeFolder = () => send<string>({ type: "app/home" });
export interface Insights {
  sessions: number;
  days: number;
  lifetimeTokens: number;
  peakDay: { day: string; tokens: number } | null;
  currentStreak: number;
  longestStreak: number;
  longestSession: { file: string; minutes: number } | null;
}

/** Settings › Notifications: one notification now. */
export const testNotification = () => send<null>({ type: "notify/test" });
/** Settings › Profile: statistics from this machine's session files (desktop/electron/src/insights.ts). */
export const insights = () => send<Insights | null>({ type: "insights" });
/** Settings › Personalization: `$CODEX_HOME/AGENTS.md`, the instructions every thread on this machine reads. */
export const readPersonal = () => send<{ path: string; text: string } | null>({ type: "personal/read" });
export const writePersonal = (text: string) => send<null>({ type: "personal/write", text });
export interface NightTask {
  id: string;
  repo: string;
  task: string;
  status: string;
  branch: string | null;
  test: string | null;
  on: string | null;
  at: string | null;
  result: unknown;
}

/** A node as Settings › Node lists it (desktop/electron/src/nodes.ts): names, never an address. */
export interface NodeEntry {
  name: string;
  id: string;
  /** What choosing it sends; null when it advertises no name to choose it by. */
  choice: string | null;
  inUse: boolean;
  model: string | null;
  contextTokens: number | null;
  state: string;
  version: string;
  coding: boolean;
}

export interface NodeList {
  thisMachine: "client" | "node" | "configured";
  nodes: NodeEntry[];
  rememberedAway: { name: string; id: string } | null;
  hint: string | null;
}

/** Settings › Node: `ling node list --json` and `ling node use <name>`, in the main process. */
export const nodes = {
  list: () => send<NodeList>({ type: "node/list" }),
  use: (choice: string) => send<string>({ type: "node/use", choice }),
};

/** The Scheduled view over Night Shift (desktop/electron/src/night.ts). */
export const night = {
  list: () => send<{ tasks: NightTask[]; reports: { name: string; modified: number }[] }>({ type: "night/list" }),
  report: (name: string) => send<string>({ type: "night/report", name }),
  add: (repo: string, task: string, test: string | null) => send<string>({ type: "night/add", repo, task, test }),
  drop: (repo: string, id: string) => send<string>({ type: "night/drop", repo, id }),
  runNow: () => send<null>({ type: "night/run" }),
};
/** Ctrl+O: the system's folder chooser; resolves with the folder, or null. */
export const chooseFolder = () => send<string | null>({ type: "dialog/open-folder" });

/** One collection of the local file index, as `ling docs status --json` describes it. */
export interface DocsCollection {
  name: string;
  root: string;
  enabled: boolean;
  default: boolean;
  counts: { documents: number; indexed: number; pending: number; failed: number; duplicates: number; skipped: number; chunks: number; embedded: number; needs_ocr_pages: number; needs_ocr_documents: number };
  last_status: string | null;
  last_run: number | null;
  disk_bytes: number;
}

export interface DocsStatus {
  collections: DocsCollection[];
  runtime_missing: string | null;
}

async function docsCall<T>(electron: object, method: "GET" | "POST", url: string, body?: object): Promise<T> {
  if (host() === "electron") return send<T>(electron);
  const response = await fetch(url, { method, credentials: "same-origin", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const answer = (await response.json().catch(() => ({}))) as T & { error?: string; said?: string };
  if (!response.ok) throw new Error(answer.error || `the local file index did not answer (${response.status})`);
  return answer;
}

/**
 * Settings › Your documents (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §8.3): the collections and
 * their state, adding a folder, removing a collection (its index, never the files) and rebuilding
 * one, all through `ling docs`: in the app by the main process, in a browser through `ling web`.
 */
export const docs = {
  status: () => docsCall<DocsStatus>({ type: "docs/status" }, "GET", "/api/docs"),
  add: (folder: string) => docsCall<unknown>({ type: "docs/add", folder }, "POST", "/api/docs/add", { folder }),
  remove: (name: string) => docsCall<unknown>({ type: "docs/remove", name }, "POST", "/api/docs/remove", { name }),
  reindex: (name: string) => docsCall<unknown>({ type: "docs/reindex", name }, "POST", "/api/docs/reindex", { name }),
};
/** `ling cavemode --json`: the level in force, where it came from, the configured one and the defaults. */
export interface CaveMode {
  level: string;
  source: string;
  configured: string | null;
  default: string;
  tuiDefault: string;
  levels: { name: string; summary: string }[];
}

/**
 * How terse answers are (specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md §11.5), through `ling cavemode`: in the
 * app by the main process, in a browser through `ling web`'s `/api/cavemode`. `set` with a thread sets
 * that thread's level; without, the level new sessions start at (`built-in` drops the configured one).
 */
export const cavemode = {
  status: (thread: string | null) =>
    docsCall<CaveMode>({ type: "cave/status", thread }, "GET", thread ? `/api/cavemode?thread=${encodeURIComponent(thread)}` : "/api/cavemode"),
  set: async (level: string, thread: string | null): Promise<string> => {
    if (host() === "electron") return send<string>({ type: "cave/set", level, thread });
    const answer = await docsCall<{ said: string }>({}, "POST", "/api/cavemode", thread ? { level, thread } : { level });
    return answer.said;
  },
};
/** Settings › Pets: a picture for the pet; null when the dialog was cancelled. */
export const chooseImage = () => send<string | null>({ type: "dialog/open-image" });

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

/**
 * Turns a recording from the microphone into text (specs/DREAMFERENCE_MIGHTLING_ASK.md §7): in a
 * browser through `ling web`'s `/api/transcribe`, in the app through its main process; both pass it
 * to the speech-to-text service on this machine. The text is for the composer, not sent anywhere.
 */
export async function transcribe(recording: Blob): Promise<string> {
  const mime = recording.type || "audio/webm";
  if (host() === "electron") {
    const answer = await send<{ text: string }>({ type: "voice/transcribe", mime, data: await base64(recording) });
    return answer.text;
  }
  const response = await fetch("/api/transcribe", { method: "POST", body: recording, headers: { "Content-Type": mime }, credentials: "same-origin" });
  const answer = (await response.json().catch(() => ({}))) as { text?: string; error?: string };
  if (!response.ok || typeof answer.text !== "string") throw new Error(answer.error || `transcription failed (${response.status})`);
  return answer.text;
}

/** Whether this page can record: a secure context (the app, or `ling web` on this machine) with a microphone API. */
export function canRecord(): boolean {
  return typeof window !== "undefined" && window.isSecureContext === true
    && typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getUserMedia === "function"
    && typeof MediaRecorder !== "undefined";
}

export interface BridgeEvents {
  message: (message: unknown) => void;
  stderr: (line: string) => void;
  protocolError: (line: string) => void;
  exit: (code: number | null) => void;
  /** The app asked for Ask or Work (its menu, a second instance, a link). */
  view?: (view: "ask" | "work") => void;
  /** A bottom-panel task's progress. */
  task?: (event: TaskEvent) => void;
  /** A `ling://` link or a second `ling app` asked for something (DESKTOP_PARITY §2.20). */
  link?: (target: LinkTarget) => void;
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
      case "link":
        return events.link?.(data.payload as LinkTarget);
      case "task://event":
        return events.task?.(data.payload as TaskEvent);
    }
  };
  window.addEventListener("message", onMessage);
  return () => window.removeEventListener("message", onMessage);
}

/** A preview tab's news from the app (desktop/electron/src/preview.ts). */
export interface PreviewEvent {
  id: string;
  kind: "state" | "ask" | "blocked" | "download" | "key";
  url?: string;
  title?: string;
  loading?: boolean;
  canGoBack?: boolean;
  canGoForward?: boolean;
  site?: string;
  reason?: string;
  error?: string | null;
  /** For `key`: a shortcut pressed while the previewed page had the focus. */
  key?: string;
}

/** What comment mode picked in a previewed page. */
export interface PickedElement {
  selector: string;
  tag: string;
  text: string;
  url: string;
}

/** The preview tab (DESKTOP_PARITY §2.9): the desktop app's only; a browser on `ling web` has none. */
export const preview = {
  available: () => host() === "electron",
  open: (id: string, url: string, thread: string | null) => send<null>({ type: "preview/open", id, url, thread }),
  navigate: (id: string, url: string, thread: string | null) => send<null>({ type: "preview/navigate", id, url, thread }),
  go: (id: string, step: "back" | "forward") => send<null>({ type: "preview/go", id, step }),
  reload: (id: string) => send<null>({ type: "preview/reload", id }),
  place: (id: string, bounds: { x: number; y: number; width: number; height: number }, visible: boolean) => send<null>({ type: "preview/place", id, bounds, visible }),
  pick: (id: string) => send<PickedElement | null>({ type: "preview/pick", id }),
  allowOnce: (id: string, site: string) => send<null>({ type: "preview/allow-once", id, site }),
  devTools: (id: string) => send<null>({ type: "preview/devtools", id }),
  close: (id: string) => send<null>({ type: "preview/close", id }),
  clear: () => send<null>({ type: "preview/clear" }),
  /** Every preview tab's events; returns the unsubscribe. */
  listen(handler: (event: PreviewEvent) => void): () => void {
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { channel?: string; payload?: PreviewEvent } | null;
      if (data && data.channel === "preview://event" && data.payload) handler(data.payload);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  },
};

/** A pull request as `gh pr list` and `gh pr view` answer (fields the tab reads). */
export interface PullRequest {
  number: number;
  title: string;
  author: { login: string } | null;
  headRefName: string;
  baseRefName: string;
  isDraft: boolean;
  reviewDecision: string | null;
  updatedAt: string;
  url: string;
  body?: string;
  state?: string;
  additions?: number;
  deletions?: number;
  changedFiles?: number;
  statusCheckRollup?: { __typename?: string; name?: string; context?: string; status?: string; conclusion?: string; state?: string; detailsUrl?: string; targetUrl?: string; workflowName?: string }[];
}

/** The pull-request tab (DESKTOP_PARITY §2.5, Phase 3 item 9): the user's `gh` in the repository, in the app only. */
export const prs = {
  available: () => host() === "electron",
  list: (root: string, inbox: "review" | "mine" | "open", thread: string | null) => send<PullRequest[]>({ type: "pr/list", root, inbox, thread }),
  view: (root: string, number: number, thread: string | null) => send<PullRequest>({ type: "pr/view", root, number, thread }),
  diff: (root: string, number: number, thread: string | null) => send<string>({ type: "pr/diff", root, number, thread }),
  review: (root: string, number: number, verdict: "comment" | "approve" | "request-changes", body: string, thread: string | null) =>
    send<string>({ type: "pr/review", root, number, verdict, body, thread }),
  status: (root: string) => send<string>({ type: "pr/status", root }),
};

/** The agent message board as the app reads it (desktop/electron/src/board.ts). */
export interface BoardPost {
  id: string;
  channel: string;
  root: string;
  author: string;
  at: number;
  text: string;
}
export interface Board {
  board: string;
  channels: { name: string; author: string; posts: number }[];
  posts: BoardPost[];
}
/** The boards subagents post to, read only; empty in a browser (the file is the app's to read). */
export async function readBoards(sqliteHome: string | null): Promise<Board[]> {
  if (host() !== "electron") return [];
  return send<Board[]>({ type: "board/read", sqliteHome });
}
