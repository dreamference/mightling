// Several repositories in one review pane (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B32): each
// repository's lines changed in the selector, and Last turn over all of them or one. The server writes
// a turn's paths from the repository holding the thread's folder, else from the folder itself; with
// repositories under the folder, a file belongs to the one whose top level holds it, and a file in
// none is shown under All repos only.

import type { FileDiff } from "./diff";

/** Lines added and removed. */
export interface Lines {
  added: number;
  removed: number;
}

const slashes = (value: string) => value.replace(/\\/g, "/").replace(/\/+$/, "");

/** A `/` path with its `.` and `..` steps taken (`/w/api/../web` is `/w/web`); never above its root. */
function withoutDots(path: string): string {
  const parts: string[] = [];
  for (const part of path.split("/")) {
    if (part === ".") continue;
    if (part !== "..") parts.push(part);
    else if (parts.length > 1 && parts[parts.length - 1] !== "") parts.pop();
  }
  return parts.join("/");
}

/** A repository's name as the selector shows it: its folder's. */
export function repoName(repo: string): string {
  return repo.split(/[\\/]/).filter(Boolean).pop() ?? repo;
}

/** A selector option: the name, then the lines changed when they are known (`fetcher +9 −3`). */
export function repoOption(name: string, lines: Lines | null | undefined): string {
  return lines ? `${name} +${lines.added} −${lines.removed}` : name;
}

/**
 * Where Last turn's paths start: the thread's folder when the repositories lie under it (the folder is
 * in none, so the server writes paths from it), else the repository (`root`) that holds the folder.
 */
export function turnBase(cwd: string | null, repos: string[], root: string | null): string | null {
  if (cwd && repos.some((repo) => slashes(repo).startsWith(`${slashes(cwd)}/`))) return cwd;
  return root ?? cwd;
}

/** The repository of `repos` (absolute top levels) a turn's file is in, the innermost one; null for none. */
export function repoOf(file: string, cwd: string | null, repos: string[]): string | null {
  const joined = /^([\\/]|[A-Za-z]:[\\/])/.test(file) ? slashes(file) : cwd ? `${slashes(cwd)}/${slashes(file)}` : null;
  if (!joined) return null;
  const absolute = withoutDots(joined);
  let best: string | null = null;
  for (const repo of repos) {
    const top = slashes(repo);
    if ((absolute === top || absolute.startsWith(`${top}/`)) && (!best || top.length > slashes(best).length)) best = repo;
  }
  return best;
}

/** What the turn changed in each repository, and in all of them (`total`, files in no repository included). */
export function turnLines(files: FileDiff[], cwd: string | null, repos: string[]): { total: Lines; byRepo: Map<string, Lines> } {
  const total = { added: 0, removed: 0 };
  const byRepo = new Map<string, Lines>(repos.map((repo) => [repo, { added: 0, removed: 0 }]));
  for (const file of files) {
    total.added += file.added;
    total.removed += file.removed;
    const repo = repoOf(file.path, cwd, repos);
    const lines = repo ? byRepo.get(repo) : undefined;
    if (lines) {
      lines.added += file.added;
      lines.removed += file.removed;
    }
  }
  return { total, byRepo };
}

/** The turn's files in one repository, or all of them (`repo` null: All repos). */
export function filesIn(files: FileDiff[], cwd: string | null, repos: string[], repo: string | null): FileDiff[] {
  return repo === null ? files : files.filter((file) => repoOf(file.path, cwd, repos) === repo);
}

// The repository service on `ling web` (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §4.3–§4.7, §10
// G4, G6, G9, G13): repositories on the node's forge, per-thread checkouts under `chat-repos/`,
// diffs, and the single-use `confirm` nonce flow for commit, push, update, merge, PR and main.

/** The named prompt `ling web`'s policy requires when starting a Work thread in a recorded checkout. */
export const CHAT_WORK_PROMPT = "chat-work";

/** LocalStorage key for this browser's stable chat id (`/api/repos/open`'s `chat`). */
export const CHAT_ID_KEY = "mightling.chatRepos.chatId";

export interface ChatRepoSummary {
  repo: string;
  default_branch: string;
  pushed_at: string;
  private: boolean;
  archived: boolean;
  mirror: boolean;
  read_only: boolean;
  checkouts: string[];
  cwd?: string;
}

export interface ChatRepoOpened {
  thread: string;
  cwd: string;
  branch: string | null;
  read_only: boolean;
  notes?: string[];
}

export interface ChatRepoFileStat {
  path: string;
  added: number;
  removed: number;
  diff?: string;
}

export interface ChatRepoBudget {
  used: number;
  limit: number;
  resets_at: number;
}

export type ChatRepoIntent = "commit" | "push" | "update" | "merge" | "pr" | "main" | "budget" | "access";

export type ChatRepoAccessLevel = "workspace" | "full";

export interface ChatRepoAccessResult {
  level: ChatRepoAccessLevel;
}

export interface ChatRepoState {
  repo: string;
  branch: string | null;
  base: string;
  ahead: number;
  behind: number;
  uncommitted: ChatRepoFileStat[];
  read_only: boolean;
  private: boolean;
  budget: ChatRepoBudget;
  warnings: string[];
  confirm?: string;
  allow_main?: boolean;
  full_access_allowed?: boolean;
  full_access?: boolean;
  merge_in_progress?: boolean;
  cwd?: string;
}

export interface ChatRepoLogEntry {
  id: string;
  subject: string;
  author: string;
  time: number;
}

export interface ChatRepoCommitResult {
  commit: string;
  files: ChatRepoFileStat[];
  added: number;
  removed: number;
}

export interface ChatRepoPushResult {
  pushed: number;
  branch: string;
  anonymity_check?: string;
}

export interface ChatRepoUpdateResult {
  updated?: boolean;
  aborted?: boolean;
  conflicts?: string[];
}

export interface ChatRepoMergeResult {
  merged: boolean;
  branch: string;
  into: string;
}

export interface ChatRepoPrResult {
  number: number;
  url: string;
}

export interface ChatRepoMainResult {
  repo: string;
  branch: string;
}

export interface AnonymityFinding {
  file: string;
  line: number;
  entry: number;
}

export class RepoRefusal extends Error {
  readonly status: number;
  readonly refused?: string;
  readonly findings?: AnonymityFinding[];
  readonly conflicts?: string[];

  constructor(
    status: number,
    message: string,
    extra: { refused?: string; findings?: AnonymityFinding[]; conflicts?: string[] } = {},
  ) {
    super(message);
    this.status = status;
    this.refused = extra.refused;
    this.findings = extra.findings;
    this.conflicts = extra.conflicts;
  }
}

export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

const defaultFetcher: Fetcher = (url, init) => fetch(url, init);
let activeFetcher: Fetcher = defaultFetcher;

/** Sets the fetcher `bridge.git` and the default `chatRepos()` client use (for tests). */
export function setChatReposFetcher(fetcher: Fetcher | null): void {
  activeFetcher = fetcher ?? defaultFetcher;
}

const recordedByCwd = new Map<string, { thread: string; repo?: string; branch?: string | null; read_only?: boolean }>();
const knownCheckoutIds = new Set<string>();
let activeChatRepoCwd: string | null = null;

/** A checkout path under `<codex_home>/chat-repos/<org>/<repo>/<bridge>-<8-hex>` (chat_repos.rs). */
const CHAT_REPO_PATH = /(?:^|[\\/])chat-repos[\\/]([^\\/]+[\\/][^\\/]+)[\\/](?:web|telegram|matrix|signal|discord)-([0-9a-f]{8})$/i;

/** Records a checkout returned by `/api/repos/open` or `/api/repos/state`. */
export function recordCheckout(entry: { thread: string; cwd: string; repo?: string; branch?: string | null; read_only?: boolean }): void {
  const norm = slashes(entry.cwd);
  if (!norm || !entry.thread) return;
  recordedByCwd.set(norm, { thread: entry.thread, repo: entry.repo, branch: entry.branch, read_only: entry.read_only });
  knownCheckoutIds.add(entry.thread);
}

/** Records the open checkout thread ids from `GET /api/repos`. */
export function recordRepos(list: readonly ChatRepoSummary[]): void {
  for (const repo of list) {
    for (const id of repo.checkouts ?? []) {
      knownCheckoutIds.add(id);
      if (repo.cwd) recordCheckout({ thread: id, cwd: repo.cwd, repo: repo.repo, read_only: repo.read_only });
    }
  }
}

/** Clears recorded checkouts and the active thread's checkout (for tests). */
export function resetChatRepos(): void {
  recordedByCwd.clear();
  knownCheckoutIds.clear();
  activeChatRepoCwd = null;
  activeFetcher = defaultFetcher;
}

/**
 * The 8-hex `chat-repos` thread id for a Work thread's `cwd`, or `null` when `cwd` is an ordinary
 * folder outside the repository service's checkouts.
 */
export function checkoutThreadForCwd(cwd: string | null | undefined): string | null {
  if (!cwd) return null;
  const norm = slashes(cwd);
  const recorded = recordedByCwd.get(norm);
  if (recorded) return recorded.thread;
  const matched = CHAT_REPO_PATH.exec(norm);
  if (matched) return matched[2].toLowerCase();
  const tail = norm.split("/").pop() ?? "";
  for (const id of knownCheckoutIds) {
    if (tail === id || tail.endsWith(`-${id}`)) return id;
  }
  return null;
}

/** Whether `cwd` is a checkout managed by the repository service (`chat-repos`). */
export function isChatRepoCwd(cwd: string | null | undefined): boolean {
  return checkoutThreadForCwd(cwd) !== null;
}

/** Sets the currently shown Work thread's folder in the web host, read by `bridge.git.available()`. */
export function setActiveChatRepoCwd(cwd: string | null | undefined): void {
  activeChatRepoCwd = cwd ? slashes(cwd) : null;
}

/** Reads the currently shown Work thread's folder in the web host. */
export function getActiveChatRepoCwd(): string | null {
  return activeChatRepoCwd;
}

/** A stable id for this browser tab/device, kept in `localStorage` for `/api/repos/open`. */
export function browserChatId(storage?: Pick<Storage, "getItem" | "setItem"> | null): string {
  const store = storage === undefined ? (typeof localStorage !== "undefined" ? localStorage : null) : storage;
  try {
    const existing = store?.getItem(CHAT_ID_KEY)?.trim();
    if (existing) return existing;
  } catch {
    // Storage unavailable: generate an in-memory id below.
  }
  const bytes = new Uint8Array(8);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  const generated = `web-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
  try {
    store?.setItem(CHAT_ID_KEY, generated);
  } catch {
    // Ignore storage quota or security errors.
  }
  return generated;
}

/** Sorts repositories with the most recently pushed first (`pushed_at`), and filters by name (`repo`). */
export function sortAndFilterRepos(repos: readonly ChatRepoSummary[], search = ""): ChatRepoSummary[] {
  const term = search.trim().toLowerCase();
  const parseTime = (iso: string): number => {
    const ms = Date.parse(iso);
    return Number.isFinite(ms) ? ms : 0;
  };
  return [...repos]
    .filter((entry) => !term || entry.repo.toLowerCase().includes(term))
    .sort((a, b) => {
      const diff = parseTime(b.pushed_at) - parseTime(a.pushed_at);
      if (diff !== 0) return diff;
      const cmp = (b.pushed_at ?? "").localeCompare(a.pushed_at ?? "");
      return cmp !== 0 ? cmp : a.repo.localeCompare(b.repo);
    });
}

/** Formats an anonymity refusal with only `<file>:<line> (entry <n>)` (§9, §10 G9). */
export function formatAnonymityRefusal(findings: readonly AnonymityFinding[] | undefined | null): string {
  if (!findings || findings.length === 0) return "Push refused by the anonymity check";
  const parts = findings.map((entry) => {
    const loc = Number.isFinite(entry.line) && entry.line > 0 ? `${entry.file}:${entry.line}` : entry.file;
    return `${loc} (entry ${entry.entry})`;
  });
  return `Push refused by the anonymity check: ${parts.join(", ")}`;
}

/** Formats the daily token budget left for `WorktreeHeader` (§7.2, §10 G13). */
export function formatBudgetLeft(budget: ChatRepoBudget): string {
  const left = Math.max(0, budget.limit - budget.used);
  return `${left.toLocaleString("en-US")} tokens left`;
}

/** A file whose diff could not be read: its header only, never invented lines. */
function minimalFileDiff(entry: ChatRepoFileStat): string {
  if (typeof entry.diff === "string" && entry.diff.trim()) return entry.diff.endsWith("\n") ? entry.diff : `${entry.diff}\n`;
  return `diff --git a/${entry.path} b/${entry.path}\n--- a/${entry.path}\n+++ b/${entry.path}\n`;
}

async function readJson(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (!text) return {};
  try {
    const parsed: unknown = JSON.parse(text);
    return typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : {};
  } catch {
    return { error: text };
  }
}

async function throwRefusal(response: Response, fallback: string): Promise<never> {
  const body = await readJson(response);
  if (body.refused === "anonymity" || Array.isArray(body.findings)) {
    const findings = Array.isArray(body.findings)
      ? body.findings.map((item) => {
          const rec = item as Record<string, unknown>;
          return { file: String(rec.file ?? ""), line: Number(rec.line ?? 0), entry: Number(rec.entry ?? 0) };
        })
      : [];
    // Without findings the check could not run. Nothing else from the answer is shown.
    const message = findings.length === 0 ? "Push refused: the anonymity check could not run" : formatAnonymityRefusal(findings);
    throw new RepoRefusal(response.status, message, { refused: "anonymity", findings });
  }
  const conflicts = Array.isArray(body.conflicts) ? body.conflicts.map(String) : undefined;
  if (conflicts && conflicts.length > 0) {
    throw new RepoRefusal(response.status, `Conflicts: ${conflicts.join(", ")}`, { conflicts });
  }
  const message = typeof body.error === "string" ? body.error : typeof body.reason === "string" ? body.reason : fallback;
  throw new RepoRefusal(response.status, message, { refused: typeof body.refused === "string" ? body.refused : undefined, conflicts });
}

export interface ChatReposApi {
  list(): Promise<{ forgeOff: boolean; repos: ChatRepoSummary[]; message?: string }>;
  open(repo: string, chat?: string, thread?: string): Promise<ChatRepoOpened>;
  state(thread: string, intent?: ChatRepoIntent): Promise<ChatRepoState>;
  diff(thread: string, scope: "uncommitted" | "branch"): Promise<string>;
  log(thread: string): Promise<ChatRepoLogEntry[]>;
  commit(thread: string, message: string, confirm: string): Promise<ChatRepoCommitResult>;
  push(thread: string, confirm: string): Promise<ChatRepoPushResult>;
  update(thread: string, confirm: string, abort?: boolean): Promise<ChatRepoUpdateResult>;
  merge(thread: string, confirm: string): Promise<ChatRepoMergeResult>;
  pr(thread: string, confirm: string): Promise<ChatRepoPrResult>;
  main(thread: string, confirm: string): Promise<ChatRepoMainResult>;
  budget(thread: string, confirm: string, add: number): Promise<ChatRepoBudget>;
  access(thread: string, level: ChatRepoAccessLevel, confirm: string, typed?: string): Promise<ChatRepoAccessResult>;
}

/** Client for `/api/repos*` on `ling web`. */
export function chatRepos(fetcher: Fetcher = activeFetcher): ChatReposApi {
  const post = async <T>(url: string, payload: Record<string, unknown>, fallback: string): Promise<T> => {
    const response = await fetcher(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) return throwRefusal(response, fallback);
    return (await response.json()) as T;
  };

  return {
    async list() {
      const response = await fetcher("/api/repos");
      if (response.status === 404) {
        const body = await readJson(response);
        const message = typeof body.error === "string" && body.error.includes("ling-admin forge on")
          ? body.error
          : "The forge is off: run `ling-admin forge on` at the node.";
        return { forgeOff: true, repos: [], message };
      }
      if (!response.ok) return throwRefusal(response, `GET /api/repos failed (${response.status})`);
      const repos = (await response.json()) as ChatRepoSummary[];
      recordRepos(repos);
      return { forgeOff: false, repos };
    },

    async open(repo, chat = browserChatId(), thread) {
      const payload: Record<string, unknown> = { repo, bridge: "web", chat };
      if (thread) payload.thread = thread;
      const opened = await post<ChatRepoOpened>("/api/repos/open", payload, "Could not open the repository");
      recordCheckout({ ...opened, repo });
      return opened;
    },

    async state(thread, intent) {
      const query = new URLSearchParams({ thread });
      if (intent) query.set("intent", intent);
      const response = await fetcher(`/api/repos/state?${query.toString()}`);
      if (!response.ok) return throwRefusal(response, `GET /api/repos/state failed (${response.status})`);
      const state = (await response.json()) as ChatRepoState;
      if (state.cwd) recordCheckout({ thread, cwd: state.cwd, repo: state.repo, branch: state.branch, read_only: state.read_only });
      return state;
    },

    async diff(thread, scope) {
      const query = new URLSearchParams({ thread, scope });
      const response = await fetcher(`/api/repos/diff?${query.toString()}`);
      if (!response.ok) return throwRefusal(response, `GET /api/repos/diff failed (${response.status})`);
      const body: unknown = await response.json();
      if (typeof body === "string") return body;
      if (typeof body === "object" && body !== null && typeof (body as { diff?: unknown }).diff === "string") {
        return (body as { diff: string }).diff;
      }
      if (!Array.isArray(body)) return "";
      const entries = body as ChatRepoFileStat[];
      const chunks: string[] = [];
      for (const entry of entries) {
        if (typeof entry.diff === "string" && entry.diff.trim()) {
          chunks.push(entry.diff.endsWith("\n") ? entry.diff : `${entry.diff}\n`);
          continue;
        }
        try {
          const fileQuery = new URLSearchParams({ thread, scope, path: entry.path });
          const fileRes = await fetcher(`/api/repos/diff?${fileQuery.toString()}`);
          if (fileRes.ok) {
            const fileBody = (await fileRes.json()) as { diff?: string };
            if (typeof fileBody.diff === "string" && fileBody.diff.trim()) {
              chunks.push(fileBody.diff.endsWith("\n") ? fileBody.diff : `${fileBody.diff}\n`);
              continue;
            }
          }
        } catch {
          // Fall back to stat hunk below when per-file diff is not mocked.
        }
        chunks.push(minimalFileDiff(entry));
      }
      return chunks.join("");
    },

    async log(thread) {
      const query = new URLSearchParams({ thread });
      const response = await fetcher(`/api/repos/log?${query.toString()}`);
      if (!response.ok) return throwRefusal(response, `GET /api/repos/log failed (${response.status})`);
      return (await response.json()) as ChatRepoLogEntry[];
    },

    commit(thread, message, confirm) {
      return post<ChatRepoCommitResult>("/api/repos/commit", { thread, message, confirm }, "Commit refused");
    },

    push(thread, confirm) {
      return post<ChatRepoPushResult>("/api/repos/push", { thread, confirm }, "Push refused");
    },

    update(thread, confirm, abort = false) {
      return post<ChatRepoUpdateResult>("/api/repos/update", abort ? { thread, confirm, abort: true } : { thread, confirm }, "Update refused");
    },

    merge(thread, confirm) {
      return post<ChatRepoMergeResult>("/api/repos/merge", { thread, confirm }, "Merge refused");
    },

    pr(thread, confirm) {
      return post<ChatRepoPrResult>("/api/repos/pr", { thread, confirm }, "Pull request refused");
    },

    main(thread, confirm) {
      return post<ChatRepoMainResult>("/api/repos/main", { thread, confirm }, "Working on main refused");
    },

    budget(thread, confirm, add) {
      return post<ChatRepoBudget>("/api/repos/budget", { thread, add, confirm }, "Budget extension refused");
    },

    access(thread, level, confirm, typed) {
      // Ending Full Access needs no nonce; a grant needs one and the typed phrase.
      const payload: Record<string, unknown> = { thread, level };
      if (confirm) payload.confirm = confirm;
      if (typed !== undefined) payload.typed = typed;
      return post<ChatRepoAccessResult>("/api/repos/access", payload, "Access change refused");
    },
  };
}

/** Whether a pull request's link may be opened: the forge's own `/forge/` path, or http(s). */
export function safePrUrl(url: string): boolean {
  if (url.startsWith("/forge/")) return true;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

export type GitBarAction =
  | "commit"
  | "push"
  | "update"
  | "abort"
  | "merge"
  | "pr"
  | "main"
  | "full-access"
  | "workspace-access";

export interface PendingGitAction {
  action: GitBarAction;
  intent: ChatRepoIntent;
  confirm: string;
  message: string;
}

/** Exact phrase the owner must type to confirm Full Access for `<org>/<repo>` (§6.2). */
export function fullAccessConfirmationPhrase(repo: string): string {
  return `FULL ACCESS ${repo}`;
}

/** Checks whether `typed` matches `FULL ACCESS <org>/<repo>` character-for-character. */
export function matchesFullAccessConfirmation(typed: string, repo: string): boolean {
  return typed === fullAccessConfirmationPhrase(repo);
}

export function intentForAction(action: GitBarAction): ChatRepoIntent {
  if (action === "abort") return "update";
  if (action === "full-access" || action === "workspace-access") return "access";
  return action;
}

/** Step 1 of every Git bar action: fetches a single-use `confirm` nonce from `/api/repos/state` with `intent=`. */
export async function beginGitAction(
  api: ChatReposApi,
  thread: string,
  action: GitBarAction,
  defaultMessage = "",
): Promise<{ state: ChatRepoState; pending: PendingGitAction }> {
  const intent = intentForAction(action);
  const state = await api.state(thread, intent);
  if (!state.confirm) throw new Error("The server did not issue a confirmation nonce.");
  return {
    state,
    pending: { action, intent, confirm: state.confirm, message: defaultMessage },
  };
}

export interface GitActionOutcome {
  text: string;
  prUrl?: string;
  conflicts?: string[];
  mergeInProgress?: boolean;
  mainRefused?: boolean;
  branch?: string;
}

/** Step 2 of every Git bar action: posts the confirmed action with `confirm` and formats the inline answer. */
export async function executeGitAction(
  api: ChatReposApi,
  thread: string,
  pending: PendingGitAction,
  options: { openTab?: (url: string) => void } = {},
): Promise<GitActionOutcome> {
  try {
    switch (pending.action) {
      case "commit": {
        const msg = pending.message.trim();
        if (!msg) throw new Error("Write a commit message first.");
        const res = await api.commit(thread, msg, pending.confirm);
        const short = res.commit.slice(0, 7);
        const count = res.files?.length ?? 0;
        return {
          text: `Committed ${short} (${count} file${count === 1 ? "" : "s"}, +${res.added} −${res.removed})`,
          mergeInProgress: false,
        };
      }
      case "push": {
        const res = await api.push(thread, pending.confirm);
        return {
          text: `Pushed ${res.pushed} commit${res.pushed === 1 ? "" : "s"} to ${res.branch}`,
        };
      }
      case "update": {
        const res = await api.update(thread, pending.confirm, false);
        if (res.conflicts && res.conflicts.length > 0) {
          return {
            text: `Conflicts: ${res.conflicts.join(", ")}`,
            conflicts: res.conflicts,
            mergeInProgress: true,
          };
        }
        return { text: "Updated from main", mergeInProgress: false };
      }
      case "abort": {
        await api.update(thread, pending.confirm, true);
        return { text: "Merge aborted", mergeInProgress: false };
      }
      case "merge": {
        const res = await api.merge(thread, pending.confirm);
        return { text: `Merged ${res.branch} into ${res.into}`, mergeInProgress: false };
      }
      case "pr": {
        const res = await api.pr(thread, pending.confirm);
        if (!safePrUrl(res.url)) return { text: `Pull request #${res.number}` };
        const openTab = options.openTab ?? ((url: string) => {
          // The app denies window.open (its links go through bridge.openWebLink, which GitBar passes in);
          // only a browser tab, `ling web`, has a window to open.
          if (typeof window !== "undefined" && window.mightlingWindowType !== "electron" && typeof window.open === "function") {
            window.open(url, "_blank", "noopener,noreferrer");
          }
        });
        openTab(res.url);
        return { text: `Pull request #${res.number}`, prUrl: res.url };
      }
      case "main": {
        const res = await api.main(thread, pending.confirm);
        return { text: `Working on ${res.branch}`, branch: res.branch };
      }
      case "full-access": {
        await api.access(thread, "full", pending.confirm, pending.message);
        return { text: "Full Access" };
      }
      case "workspace-access": {
        await api.access(thread, "workspace", pending.confirm);
        return { text: "Workspace" };
      }
    }
  } catch (error) {
    if (error instanceof RepoRefusal) {
      if (error.refused === "anonymity" || error.findings) {
        return { text: error.findings && error.findings.length > 0 ? formatAnonymityRefusal(error.findings) : error.message };
      }
      if (error.conflicts && error.conflicts.length > 0) {
        return {
          text: `Conflicts: ${error.conflicts.join(", ")}`,
          conflicts: error.conflicts,
          mergeInProgress: true,
        };
      }
      if (pending.action === "main" && error.status === 403) {
        return { text: error.message, mainRefused: true };
      }
      return { text: error.message };
    }
    throw error;
  }
}

/**
 * Opens a repository via `POST /api/repos/open` and starts or resumes a Work thread in its `cwd`
 * with prompt `chat-work` (§10 G4).
 */
export async function openRepoForWork<TThread extends { id: string; cwd: string }>(
  repo: string,
  options: {
    api?: ChatReposApi;
    chat?: string;
    servedModel?: string | null;
    existingThreads?: readonly TThread[];
    request: (method: "thread/start" | "thread/resume", params: Record<string, unknown>) => Promise<{ thread: TThread }>;
  },
): Promise<{ opened: ChatRepoOpened; thread: TThread; resumed: boolean }> {
  const api = options.api ?? chatRepos();
  const opened = await api.open(repo, options.chat ?? browserChatId());
  setActiveChatRepoCwd(opened.cwd);
  const existing = (options.existingThreads ?? []).find((entry) => slashes(entry.cwd) === slashes(opened.cwd));
  if (existing) {
    const resumed = await options.request("thread/resume", {
      threadId: existing.id,
      ...(options.servedModel ? { model: options.servedModel } : {}),
    });
    return { opened, thread: resumed.thread, resumed: true };
  }
  const started = await options.request("thread/start", {
    cwd: opened.cwd,
    prompt: CHAT_WORK_PROMPT,
    ...(options.servedModel ? { model: options.servedModel } : {}),
  });
  return { opened, thread: started.thread, resumed: false };
}

