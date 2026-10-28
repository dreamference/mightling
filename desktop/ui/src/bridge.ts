// The app window's end of the main process (desktop/electron/src/main.ts): one object the
// preload exposes, `window.electronBridge.sendMessageFromView(message)`, and window `MessageEvent`s
// for what the main process sends — the Codex app's shape, so this page could also run in a
// browser against another transport. Nothing else in the UI touches Electron.

import { chatRepos, checkoutThreadForCwd, getActiveChatRepoCwd, isChatRepoCwd } from "./repos";
import type { Transport } from "./rpc";
import type { TerminalSession } from "./terminal";
import type { PaneKind, WindowsState } from "./windows";

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
/** What the main process asks of the window in front: the Window menu's panes, and File's New thread, Open folder and Settings (DESKTOP §16 B20). */
export const WINDOW_COMMANDS = ["detachTerminal", "detachReview", "newChat", "openFolder", "settings"] as const;
export type WindowCommand = (typeof WINDOW_COMMANDS)[number];

export interface LinkTarget extends WorkTarget {
  prompt?: string | null;
  page?: "new" | "settings" | "skills" | "night" | "plugins" | "pets" | "ask" | "quick";
  plugin?: string;
  /** With `page: "ask"`: send the prompt as a new question at once (Quick Entry). */
  send?: boolean;
  /** `ling://settings/<page>` (DESKTOP §16 B19). */
  settingsPage?: string;
  /** `ling://new?originUrl=…`: the remote whose project the new thread starts in. */
  origin?: string;
}

/** Whether a link's folder is an absolute folder that exists (DESKTOP §16 B19); in a browser, not checked. */
export const folderExists = (dir: string) => (host() === "electron" ? send<boolean>({ type: "folder/check", dir }) : Promise.resolve(true));
/** A project folder's `origin` remote, or null. */
export const originOf = (dir: string) => send<string | null>({ type: "git/origin", dir });

export interface Airgapped {
  level: "off" | "on";
  source: string;
}

/** A query deep link (`?open=repos`, `?open=repo:<org>/<repo>`, `?open=thread:<id>` with optional `&approval=1`, CHAT_REPOS §12.4 G3). */
export type OpenTarget =
  | { kind: "repos"; approval: boolean }
  | { kind: "repo"; repo: string; approval: boolean }
  | { kind: "thread"; threadId: string; approval: boolean };

function validRepoPart(part: string): boolean {
  return (
    part.length >= 1 &&
    part.length <= 64 &&
    !part.startsWith(".") &&
    !part.startsWith("-") &&
    /^[A-Za-z0-9._-]+$/.test(part)
  );
}

/** Matches `chat_repos.rs`'s `valid_repo_name`: `<org>/<repo>` with two safe components. */
export function validRepoName(name: string): boolean {
  const slash = name.indexOf("/");
  if (slash <= 0 || name.indexOf("/", slash + 1) !== -1) return false;
  return validRepoPart(name.slice(0, slash)) && validRepoPart(name.slice(slash + 1));
}

/** An 8-char lowercase hex chat-repos thread id or a uuid-like app-server thread id (<= 64 chars). */
export function validOpenThreadId(id: string): boolean {
  if (/^[0-9a-f]{8}$/.test(id)) return true;
  return (
    id.length >= 2 &&
    id.length <= 64 &&
    !id.startsWith("-") &&
    !id.endsWith("-") &&
    id.includes("-") &&
    /^[0-9a-fA-F-]+$/.test(id)
  );
}

/** Parses `?open=...` and optional `&approval=1` from a query string; returns `null` for anything else. */
export function parseOpenTarget(search: string): OpenTarget | null {
  const params = new URLSearchParams(search || "");
  const raw = params.get("open");
  if (!raw) return null;
  const approval = params.get("approval") === "1";
  if (raw === "repos") return { kind: "repos", approval };
  if (raw.startsWith("repo:")) {
    const repo = raw.slice(5);
    return validRepoName(repo) ? { kind: "repo", repo, approval } : null;
  }
  if (raw.startsWith("thread:")) {
    const threadId = raw.slice(7);
    return validOpenThreadId(threadId) ? { kind: "thread", threadId, approval } : null;
  }
  return null;
}

/** Reads `?open=...` from `loc.search` and removes `open` and `approval` with `hist.replaceState`. */
export function consumeOpenTarget(
  loc: { pathname: string; search: string; hash?: string } | null = typeof window !== "undefined" ? window.location : null,
  hist: { replaceState(data: unknown, unused: string, url?: string | URL | null): void } | null = typeof window !== "undefined" ? window.history : null,
): OpenTarget | null {
  if (!loc) return null;
  const search = loc.search || "";
  const params = new URLSearchParams(search);
  if (!params.has("open") && !params.has("approval")) return null;
  const parsed = parseOpenTarget(search);
  params.delete("open");
  params.delete("approval");
  const rest = params.toString();
  const hash = loc.hash && !loc.hash.includes("tgWebApp") ? loc.hash : "";
  const nextUrl = loc.pathname + (rest ? `?${rest}` : "") + hash;
  try {
    hist?.replaceState(null, "", nextUrl);
  } catch {
    // Ignore when history is unavailable.
  }
  return parsed;
}

let cachedOpenTarget: OpenTarget | null | undefined;

/** The deep-link target handed over by `bridge.js` after its launch check, or read from `location.search` once the session is ready. */
export function openTarget(): OpenTarget | null {
  if (cachedOpenTarget !== undefined) return cachedOpenTarget;
  if (typeof window !== "undefined" && typeof window.electronBridge?.openTarget === "function") {
    const fromBridge = window.electronBridge.openTarget();
    if (fromBridge) {
      cachedOpenTarget = fromBridge;
      return fromBridge;
    }
  }
  cachedOpenTarget = consumeOpenTarget();
  return cachedOpenTarget;
}

export interface OpenTargetActions {
  openRepos(repo: string | null): void;
  openWorkThread(threadId: string): void;
  setApprovalRequested(requested: boolean): void;
}

/** Routes a validated `OpenTarget` to the page's Repos hook, Work thread view, and G7 approval flag. */
export function applyOpenTarget(target: OpenTarget | null, actions: OpenTargetActions): void {
  if (!target) return;
  if (target.approval) actions.setApprovalRequested(true);
  if (target.kind === "repos") actions.openRepos(null);
  else if (target.kind === "repo") actions.openRepos(target.repo);
  else if (target.kind === "thread") actions.openWorkThread(target.threadId);
}

/** What the preload exposes (desktop/electron/src/preload.ts). */
interface ElectronBridge {
  sendMessageFromView(message: unknown): Promise<unknown>;
  /** Where a dropped file lives on disk; "" when it has no path (older preloads lack it). */
  pathForFile?(file: File): string;
  /** Deep-link target parsed by `bridge.js` after the launch check passed. */
  openTarget?(): OpenTarget | null;
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

export type GitScope = "unstaged" | "staged" | "commit" | "branch" | "uncommitted";

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

function webRepoThread(folder?: string | null): string | null {
  return checkoutThreadForCwd(folder ?? getActiveChatRepoCwd());
}

/**
 * Window-side git (desktop/electron/src/git.ts) in the desktop app, and `/api/repos/diff` +
 * `/api/repos/state` for a `chat-repos` thread in `ling web` (CHAT_REPOS §10 G6); an ordinary
 * browser thread shows only the review pane's Last turn scope from the server.
 */
export const git = {
  // Without a folder named this answers for the window: the desktop app only, as every caller that
  // gates Electron-only pieces on it expects. A `chat-repos` folder is git in the web host too.
  available: (cwd?: string | null) => host() === "electron" || isChatRepoCwd(cwd),
  repos: (cwd: string) => {
    if (host() === "electron") return send<string[]>({ type: "git/repos", cwd });
    return Promise.resolve(isChatRepoCwd(cwd) ? [cwd] : []);
  },
  /** Why git cannot run on this machine (a Mac without the Command Line Tools), or null. */
  missing: () => (host() === "electron" ? send<string | null>({ type: "git/missing" }) : Promise.resolve(null)),
  status: async (root: string): Promise<GitStatus> => {
    if (host() === "electron") return send<GitStatus>({ type: "git/status", root });
    const thread = webRepoThread(root);
    if (!thread) throw new Error("This folder is not a checked-out repository.");
    const state = await chatRepos().state(thread);
    return {
      root,
      branch: state.branch,
      head: null,
      upstream: state.base ? `origin/${state.base}` : null,
      ahead: state.ahead,
      behind: state.behind,
      files: (state.uncommitted ?? []).map((entry) => ({
        path: entry.path,
        staged: ".",
        unstaged: "M",
        from: null,
      })),
    };
  },
  diff: async (root: string, scope: GitScope, commit: string | null = null): Promise<{ scope: GitScope; diff: string; against: string | null }> => {
    if (host() === "electron") return send<{ scope: GitScope; diff: string; against: string | null }>({ type: "git/diff", root, scope, commit });
    const thread = webRepoThread(root);
    if (!thread) throw new Error("This folder is not a checked-out repository.");
    const repoScope: "uncommitted" | "branch" = scope === "branch" ? "branch" : "uncommitted";
    const diff = await chatRepos().diff(thread, repoScope);
    return { scope, diff, against: repoScope === "branch" ? "main" : null };
  },
  log: async (root: string): Promise<GitCommit[]> => {
    if (host() === "electron") return send<GitCommit[]>({ type: "git/log", root });
    const thread = webRepoThread(root);
    if (!thread) return [];
    const entries = await chatRepos().log(thread);
    return entries.map((entry) => ({ sha: entry.id, subject: entry.subject, author: entry.author, time: entry.time }));
  },
  /** The lines the repository's tracked files changed against HEAD (DESKTOP §16 B32). */
  changes: async (root: string): Promise<{ added: number; removed: number }> => {
    if (host() === "electron") return send<{ added: number; removed: number }>({ type: "git/changes", root });
    const thread = webRepoThread(root);
    if (!thread) return { added: 0, removed: 0 };
    const state = await chatRepos().state(thread);
    return (state.uncommitted ?? []).reduce(
      (acc, file) => ({ added: acc.added + file.added, removed: acc.removed + file.removed }),
      { added: 0, removed: 0 },
    );
  },
  stage: (root: string, paths: string[]) => send<void>({ type: "git/stage", root, paths }),
  unstage: (root: string, paths: string[]) => send<void>({ type: "git/unstage", root, paths }),
  discard: (root: string, paths: string[]) => send<void>({ type: "git/discard", root, paths }),
  apply: (root: string, patch: string, options: { cached?: boolean; reverse?: boolean; index?: boolean; zeroContext?: boolean }) => send<void>({ type: "git/apply", root, patch, ...options }),
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
/**
 * Opens a web address from an answer, once the user said so (LinkDialog.tsx): in the app in the
 * system's browser through the main process, which refuses at `/airgapped on`; in a browser on
 * `ling web` in a new tab, with no opener and no referrer.
 */
export function openWebLink(url: string, thread: string | null): Promise<unknown> {
  if (host() === "electron") return send<string>({ type: "open/url", url, thread });
  window.open(url, "_blank", "noopener,noreferrer");
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
/** Shows a file in the system's file manager (DESKTOP §16 B14); the app only. */
export const revealFile = (path: string) => send<null>({ type: "open/reveal", path });

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

/** What a detached pane asks of the window it came from (desktop/electron/src/windows.ts `paneAction`). */
export type PaneAction =
  | { kind: "newTerminal"; thread: string }
  | { kind: "compose"; thread: string; text: string }
  | { kind: "openFile"; thread: string; path: string; line: number | null };

/**
 * Several windows (the app only; DESKTOP_ELECTRON §13): a new one, what this one shows (the main
 * process gives a thread's composer to one window), taking the composer over, a pane detached into
 * a window of its own and docked back, and a detached pane's requests for the window it came from.
 */
export const windows = {
  available: () => host() === "electron",
  open: (view: "ask" | "work") => send<void>({ type: "window/new", view }),
  state: () => send<WindowsState>({ type: "window/state" }),
  showing: (view: "ask" | "work", thread: string | null) => send<WindowsState>({ type: "window/showing", view, thread }),
  take: (thread: string) => send<WindowsState>({ type: "window/take", thread }),
  showThread: (thread: string) => send<void>({ type: "window/show-thread", thread }),
  detach: (pane: PaneKind, thread: string) => send<void>({ type: "window/detach", pane, thread }),
  dock: (pane: PaneKind, thread: string) => send<void>({ type: "window/dock", pane, thread }),
  /** A detached pane's request; the main process names the pane's thread. */
  relay: (action: { kind: "newTerminal" } | { kind: "compose"; text: string } | { kind: "openFile"; path: string; line: number | null }) => send<void>({ type: "pane/relay", action }),
};

/** The terminal tabs every window sees (desktop/electron/src/terminal-mirror.ts): the app only. */
export const terminals = {
  available: () => host() === "electron",
  list: () => send<TerminalSession[]>({ type: "terminal/list" }),
  publish: (session: TerminalSession) => send<void>({ type: "terminal/publish", session }),
  forget: (processId: string) => send<void>({ type: "terminal/forget", processId }),
  /** A tab's recent output, base64, kept by the main process from the start. */
  backlog: (processId: string) => send<string>({ type: "terminal/backlog", processId }),
};
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
  /** DESKTOP §16 B40: how many times the runner started it, and when `ling night apply` brought it into a checkout. */
  attempts?: number | null;
  applied?: string | null;
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
/** A repeating Night Shift task (desktop/electron/src/night.ts). */
export interface RepeatingTask {
  id: string;
  repo: string;
  task: string;
  test: string | null;
  cadence: "daily" | "weekly" | "days" | "every" | "monthly";
  weekday: string | null;
  days: string[] | null;
  every: number | null;
  day: number | null;
  lastQueued: string | null;
  lastError: string | null;
}
export const night = {
  list: () => send<{ tasks: NightTask[]; reports: { name: string; modified: number }[] }>({ type: "night/list" }),
  report: (name: string, archived = false) => send<string>({ type: "night/report", name, ...(archived ? { archived } : {}) }),
  add: (repo: string, task: string, test: string | null, options: { on?: string | null; bestOf?: number | null } = {}) =>
    send<string>({ type: "night/add", repo, task, test, on: options.on ?? null, bestOf: options.bestOf ?? null }),
  /** Whether Night Shift runs by itself, and its window (DESKTOP §16 B5). */
  status: () => send<{ timer: "on" | "off" | "unsupported"; window: string | null }>({ type: "night/status" }),
  drop: (repo: string, id: string) => send<string>({ type: "night/drop", repo, id }),
  runNow: () => send<null>({ type: "night/run" }),
  /** Repeating tasks: every night, every week on one day, on chosen weekdays, every N nights or on a day of the month (ling-admin night every); the main process checks each (cadence.ts). */
  every: (repo: string, task: string, test: string | null, cadence: RepeatingTask["cadence"], on: { weekday?: string; days?: string[]; every?: number; day?: number }) =>
    send<string>({ type: "night/every-add", repo, task, test, cadence, weekday: on.weekday ?? null, days: on.days ?? null, every: on.every ?? null, day: on.day ?? null }),
  repeating: () => send<RepeatingTask[]>({ type: "night/every-list" }),
  dropRepeating: (id: string) => send<string>({ type: "night/every-drop", id }),
  /** A finished task's last message, diff and transcript, and the session it ran as. */
  details: (id: string, archived = false) => send<{ result: string | null; diff: string | null; transcript: string | null; session: string | null }>({ type: "night/details", id, ...(archived ? { archived } : {}) }),
  /** DESKTOP §16 B40: `ling night apply <id>` in a thread's folder, and the task's block in the newest report naming it. */
  apply: (cwd: string, id: string) => send<string>({ type: "night/apply", cwd, id }),
  note: (id: string) => send<{ report: string; text: string } | null>({ type: "night/note", id }),
  /** DESKTOP §16 B41: runs and reports in `night/archive/` holding every word (the newest with none). */
  archive: (query: string) => send<{ tasks: NightTask[]; reports: { name: string; lines: string[] }[] }>({ type: "night/archive", query }),
};
/** Ctrl+O: the system's folder chooser; resolves with the folder, or null. */
export const chooseFolder = () => send<string | null>({ type: "dialog/open-folder" });
/** Saves a thread's export where the user chooses (desktop app); the path, or null when cancelled. */
export const saveText = (name: string, text: string) => send<string | null>({ type: "dialog/save-text", name, text });

/** A project's knowledge files (desktop app only: `ling web` has no main process to keep them). */
export interface KnowledgeFile {
  name: string;
  path: string;
  size: number;
}
export const knowledge = {
  available: () => host() === "electron",
  list: (projectId: string) => send<KnowledgeFile[]>({ type: "project/knowledge-list", projectId }),
  add: (projectId: string) => send<KnowledgeFile[]>({ type: "project/knowledge-add", projectId }),
  remove: (projectId: string, name: string) => send<KnowledgeFile[]>({ type: "project/knowledge-remove", projectId, name }),
};

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

/**
 * Whether the page offers the forge's Code view (FORGEJO §2.4): in a browser on `ling web`, when the
 * node has a forge and this device may see it signed in (`control`). The app's window has none yet.
 */
export async function forgeAvailable(): Promise<boolean> {
  if (host() === "electron") return false;
  const response = await fetch("/api/forge", { credentials: "same-origin" });
  if (!response.ok) return false;
  const answer = (await response.json().catch(() => ({}))) as { available?: boolean; control?: boolean };
  return answer.available === true && answer.control === true;
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
/** Settings › Pets: a picture for the pet; null when the dialog was cancelled. */
export const chooseImage = () => send<string | null>({ type: "dialog/open-image" });

export interface Uploaded {
  path: string;
  bytes: number;
}

/** A file's bytes as base64, built in pieces so a large file does not overflow the argument list. */
/**
 * A pasted image for a Work turn (DESKTOP §16 B13): the app's main process writes it under
 * `$CODEX_HOME/attachments/` and answers its path; `ling web` has no such route, so in a browser it fails.
 */
export async function workAttach(file: Blob & { name?: string }, kind: "image" | "file", name: string): Promise<string> {
  if (host() !== "electron") throw new Error("Pasted images reach Work in the desktop app; in a browser, drop the image's file or ask in Ask.");
  return (await send<{ path: string }>({ type: "work/attach", name, kind, data: await base64(file) })).path;
}

export async function base64(file: Blob): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

/**
 * An incognito question (DESKTOP_PARITY_CLAUDE §4.3), the desktop app's only: its folder marked when
 * it starts, and deleted, with every file in it, when the question is left.
 */
export const incognito = {
  available: () => host() === "electron",
  mark: (thread: string) => send<null>({ type: "ask/incognito", thread }),
  forget: (thread: string) => send<boolean>({ type: "ask/forget", thread }),
};

/**
 * Earlier questions for the agent (DESKTOP_PARITY_CLAUDE §4.4), the desktop app's only: the copies the
 * main process keeps while Settings › Memories has it on.
 */
/** The memory files (DESKTOP §16 B17): the summary and the index the memory pipeline writes; the app only. */
export interface MemoryFile {
  name: string;
  about: string;
  text: string;
  modified: number;
  cut: boolean;
}
export const memoryFiles = {
  available: () => host() === "electron",
  list: () => send<MemoryFile[]>({ type: "memories/files" }),
  save: (name: string, text: string) => send<null>({ type: "memories/save", name, text }),
};

/** A setting an MCP bundle asks for (desktop/electron/src/mcpb.ts `BundleSetting`). */
export interface BundleSetting {
  key: string;
  type: "string" | "number" | "boolean" | "directory" | "file";
  title: string;
  description: string;
  required: boolean;
  sensitive: boolean;
  multiple: boolean;
  default: string | number | boolean | string[] | null;
  min: number | null;
  max: number | null;
}
/** An MCP bundle's manifest as the main process read it (mcpb.ts `BundleSummary`). */
export interface BundleSummary {
  name: string;
  dir: string;
  displayName: string;
  version: string;
  description: string;
  author: string;
  serverType: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  settings: BundleSetting[];
  signed: boolean;
  files: number;
  unpackedBytes: number;
  fetches: string | null;
  refusal: string | null;
  installed: boolean;
}
export interface BundleServer {
  command: string;
  args: string[];
  env?: Record<string, string>;
  cwd: string;
}
export interface InstalledBundle {
  name: string;
  displayName: string;
  version: string;
  author: string;
  dir: string;
}
/** MCP bundles installed from a file (DESKTOP §16 B45): the desktop app unpacks them; a browser cannot. */
export const mcpBundles = {
  available: () => host() === "electron",
  pick: () => send<{ token: string; file: string; summary: BundleSummary } | null>({ type: "mcpb/pick" }),
  install: (token: string, values: Record<string, unknown>, replace: boolean) => send<{ name: string; dir: string; server: BundleServer }>({ type: "mcpb/install", token, values, replace }),
  remove: (name: string) => send<null>({ type: "mcpb/remove", name }),
  list: () => send<InstalledBundle[]>({ type: "mcpb/list" }),
};

export const recall = {
  available: () => host() === "electron",
  index: () => send<Record<string, number>>({ type: "recall/index" }),
  write: (thread: string, updatedAt: number, markdown: string) => send<null>({ type: "recall/write", thread, updatedAt, markdown }),
  prune: (keep: string[]) => send<number>({ type: "recall/prune", keep }),
};

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
  /** Who holds which thread's composer, whenever it changes (several windows). */
  windows?: (state: WindowsState) => void;
  /** Every terminal tab of the app, whenever the list changes. */
  terminals?: (list: TerminalSession[]) => void;
  /** A pane detached from this window closed: show it here again. */
  docked?: (pane: PaneKind, thread: string) => void;
  /** A pane detached from this window asks for something. */
  paneAction?: (action: PaneAction) => void;
  /** Another device acted on a thread here (sync, specs/DREAMFERENCE_MIGHTLING_SYNC.md §18.2): "Approved from the phone". */
  syncNotice?: (notice: { text: string; thread: string | null }) => void;
  /** A query deep link (`?open=…`) parsed after the launch check passed (CHAT_REPOS §12.4 G3). */
  openTarget?: (target: OpenTarget) => void;
  /** The Window menu's pane items and File's New Thread, Open Folder… and Settings…, for this window. */
  command?: (command: WindowCommand) => void;
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
      case "windows":
        return events.windows?.(data.payload as WindowsState);
      case "terminals":
        return events.terminals?.(Array.isArray(data.payload) ? (data.payload as TerminalSession[]) : []);
      case "pane://docked": {
        const docked = data.payload as { pane?: unknown; thread?: unknown } | null;
        if ((docked?.pane === "terminal" || docked?.pane === "review") && typeof docked.thread === "string") events.docked?.(docked.pane, docked.thread);
        return;
      }
      case "pane://action":
        return events.paneAction?.(data.payload as PaneAction);
      case "sync://notice":
        return events.syncNotice?.(data.payload as { text: string; thread: string | null });
      case "open-target":
        if (data.payload && typeof data.payload === "object") events.openTarget?.(data.payload as OpenTarget);
        return;
      case "command":
        if (typeof data.payload === "string" && (WINDOW_COMMANDS as readonly string[]).includes(data.payload)) events.command?.(data.payload as WindowCommand);
        return;
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
  /** A dragged area instead of one element (DESKTOP §16 B29), in the page's CSS pixels. */
  area?: { x: number; y: number; width: number; height: number };
}

/** An office file as a PDF (DESKTOP §16 B31): the desktop app converts it with LibreOffice, no network; a browser cannot. */
export const office = {
  available: () => host() === "electron",
  preview: (path: string) => send<{ pdf: string } | { unavailable: string }>({ type: "office/preview", path }),
};

/** The preview tab (DESKTOP_PARITY §2.9): the desktop app's only; a browser on `ling web` has none. */
export const preview = {
  /** The recent console of a thread's previews (or of every preview), for the agent's console tool. */
  console: (thread: string | null) => send<{ url: string; lines: string[] }[]>({ type: "preview/console", thread }),
  available: () => host() === "electron",
  open: (id: string, url: string, thread: string | null) => send<null>({ type: "preview/open", id, url, thread }),
  navigate: (id: string, url: string, thread: string | null) => send<null>({ type: "preview/navigate", id, url, thread }),
  go: (id: string, step: "back" | "forward") => send<null>({ type: "preview/go", id, step }),
  reload: (id: string, fresh = false) => send<null>({ type: "preview/reload", id, fresh }),
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
  /** DESKTOP §16 B32: the comments and reviews (`gh pr view --json comments,reviews`), and Create pull request (`gh pr create`). */
  activity: (root: string, number: number, thread: string | null) => send<PullRequestActivity>({ type: "pr/activity", root, number, thread }),
  create: (root: string, draft: { title: string; body: string; base: string | null; draft: boolean }, thread: string | null) => send<string>({ type: "pr/create", root, draft, thread }),
};

/** A pull request's conversation as `gh pr view --json comments,reviews` answers (the fields the tab reads). */
export interface PullRequestActivity {
  comments?: { author: { login: string } | null; body: string; createdAt: string; url?: string }[];
  reviews?: { author: { login: string } | null; body: string; state: string; submittedAt: string | null }[];
}

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

/** The Telegram Mini App host (`ling-rs/web/static/bridge.js`, CHAT_REPOS §10 G5): a no-op in the desktop app and in an ordinary browser tab. */
export const telegram = {
  available(): boolean {
    if (host() === "electron") return false;
    return typeof document !== "undefined" && document.documentElement?.dataset?.host === "telegram";
  },
  setBackButton(visible: boolean): void {
    if (!telegram.available()) return;
    if (typeof window === "undefined" || !window.electronBridge) return;
    void window.electronBridge.sendMessageFromView({
      type: "telegram/back-button",
      visible: Boolean(visible),
    });
  },
  onBack(listener: () => void): () => void {
    if (!telegram.available() || typeof window === "undefined" || typeof window.addEventListener !== "function") {
      return () => {};
    }
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { channel?: string } | null;
      if (data && data.channel === "telegramBack") listener();
    };
    window.addEventListener("message", onMessage);
    return () => {
      if (typeof window !== "undefined" && typeof window.removeEventListener === "function") {
        window.removeEventListener("message", onMessage);
      }
    };
  },
};

