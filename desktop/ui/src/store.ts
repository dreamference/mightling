// What the Work window shows, as a pure function of what the server said
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §6.1–§6.3). No I/O here: App.tsx feeds the reducer the
// bridge's events and the answers to its requests, and the tests feed it a scripted session.

import type { RequestId } from "./protocol/RequestId";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { ServerRequest } from "./protocol/ServerRequest";
import type { PermissionProfileSummary } from "./protocol/v2/PermissionProfileSummary";
import type { QueuedSubmission } from "./protocol/v2/QueuedSubmission";
import type { ThreadGoal } from "./protocol/v2/ThreadGoal";
import type { Thread } from "./protocol/v2/Thread";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import type { ThreadTokenUsage } from "./protocol/v2/ThreadTokenUsage";
import type { Turn } from "./protocol/v2/Turn";
import type { TurnError } from "./protocol/v2/TurnError";
import type { TurnStatus } from "./protocol/v2/TurnStatus";

import { isAskThread } from "./ask";

export interface TurnView {
  id: string;
  status: TurnStatus;
  items: ThreadItem[];
  error: TurnError | null;
  /** The turn's whole diff, from `turn/diff/updated`. */
  diff: string | null;
}

export interface ThreadView {
  thread: Thread;
  turns: TurnView[];
  /** The turn running now, which a new message steers and Stop interrupts. */
  activeTurnId: string | null;
  usage: ThreadTokenUsage | null;
}

export interface PendingRequest {
  id: RequestId;
  method: ServerRequest["method"];
  params: ServerRequest["params"];
  threadId: string | null;
}

export type ServerStatus = "stopped" | "starting" | "ready" | "exited";

/** How the requests still waiting fail when `ling app-server` exits (App.tsx), and so how their
 * notices begin: a server that is ready again takes them back. */
export const SERVER_EXITED = "the agent's server exited";

export interface WorkState {
  server: ServerStatus;
  /** The launcher's own messages: the wait for the model server, shown on the start-up screen. */
  stderr: string[];
  /** Lines on the protocol channel that were not protocol messages. */
  protocolErrors: string[];
  threads: Record<string, ThreadView>;
  selected: string | null;
  requests: PendingRequest[];
  notices: string[];
  /** Each thread's queued follow-ups (`thread/queue/list`), refreshed on `thread/queue/changed`. */
  queues: Record<string, QueuedSubmission[]>;
  /** Each thread's goal (`thread/goal/get`, `thread/goal/updated`, `thread/goal/cleared`). */
  goals: Record<string, ThreadGoal | null>;
}

export const initialState: WorkState = {
  server: "stopped",
  stderr: [],
  protocolErrors: [],
  threads: {},
  selected: null,
  requests: [],
  notices: [],
  queues: {},
  goals: {},
};

export type Action =
  | { type: "server"; status: ServerStatus }
  | { type: "stderr"; line: string }
  | { type: "protocolError"; line: string }
  | { type: "threads"; list: Thread[] }
  | { type: "opened"; thread: Thread }
  | { type: "select"; threadId: string | null }
  | { type: "notification"; notification: ServerNotification }
  | { type: "serverRequest"; request: ServerRequest }
  | { type: "answered"; id: RequestId }
  | { type: "archived"; threadId: string }
  | { type: "notice"; message: string }
  | { type: "queue"; threadId: string; items: QueuedSubmission[] }
  | { type: "goal"; threadId: string; goal: ThreadGoal | null }
  | { type: "dismissNotice"; index: number }
  /** Takes back a notice whose cause is gone (a folder now named), wherever it is in the list. */
  | { type: "dropNotice"; message: string };

const STDERR_KEPT = 200;

const sameId = (a: RequestId, b: RequestId): boolean => JSON.stringify(a) === JSON.stringify(b);

function turnView(turn: Turn): TurnView {
  return { id: turn.id, status: turn.status, items: [...turn.items], error: turn.error, diff: null };
}

function emptyView(thread: Thread): ThreadView {
  return { thread, turns: thread.turns.map(turnView), activeTurnId: activeTurn(thread.turns), usage: null };
}

function activeTurn(turns: Turn[]): string | null {
  return turns.find((turn) => turn.status === "inProgress")?.id ?? null;
}

/** Applies `change` to the thread `threadId`, if this window knows it. */
function withThread(state: WorkState, threadId: string, change: (view: ThreadView) => ThreadView): WorkState {
  const view = state.threads[threadId];
  if (!view) return state;
  return { ...state, threads: { ...state.threads, [threadId]: change(view) } };
}

/** Applies `change` to the turn `turnId`, creating it when a notification names a turn not yet seen. */
function withTurn(view: ThreadView, turnId: string, change: (turn: TurnView) => TurnView): ThreadView {
  const index = view.turns.findIndex((turn) => turn.id === turnId);
  const turns = [...view.turns];
  if (index < 0) {
    turns.push(change({ id: turnId, status: "inProgress", items: [], error: null, diff: null }));
  } else {
    turns[index] = change(turns[index]);
  }
  return { ...view, turns };
}

function upsertItem(turn: TurnView, item: ThreadItem): TurnView {
  const index = turn.items.findIndex((existing) => existing.id === item.id);
  const items = [...turn.items];
  if (index < 0) items.push(item);
  else items[index] = item;
  return { ...turn, items };
}

function editItem(turn: TurnView, itemId: string, change: (item: ThreadItem) => ThreadItem): TurnView {
  const index = turn.items.findIndex((existing) => existing.id === itemId);
  if (index < 0) return turn;
  const items = [...turn.items];
  items[index] = change(items[index]);
  return { ...turn, items };
}

function appendAt(list: string[], index: number, delta: string): string[] {
  const next = [...list];
  while (next.length <= index) next.push("");
  next[index] += delta;
  return next;
}

/**
 * A thread's first question becomes its preview here as it is sent, as ling's thread/list has it
 * after a restart: a new thread is titled by it at once, not "New thread" until the app restarts.
 */
function withPreview(view: ThreadView, item: ThreadItem): ThreadView {
  if (view.thread.preview || item.type !== "userMessage") return view;
  const text = item.content.map((input) => (input.type === "text" ? input.text : "")).join(" ").trim();
  return text ? { ...view, thread: { ...view.thread, preview: text } } : view;
}

function onNotification(state: WorkState, notification: ServerNotification): WorkState {
  switch (notification.method) {
    case "thread/started": {
      const thread = notification.params.thread;
      if (state.threads[thread.id]) return state;
      return { ...state, threads: { ...state.threads, [thread.id]: emptyView(thread) } };
    }
    case "thread/status/changed": {
      const { threadId, status } = notification.params;
      return withThread(state, threadId, (view) => ({ ...view, thread: { ...view.thread, status } }));
    }
    case "thread/name/updated": {
      const { threadId, threadName } = notification.params;
      return withThread(state, threadId, (view) => ({ ...view, thread: { ...view.thread, name: threadName ?? null } }));
    }
    case "turn/started": {
      const { threadId, turn } = notification.params;
      return withThread(state, threadId, (view) => ({
        ...withTurn(view, turn.id, (existing) => ({ ...existing, status: turn.status, error: turn.error })),
        activeTurnId: turn.id,
      }));
    }
    case "turn/completed": {
      const { threadId, turn } = notification.params;
      return withThread(state, threadId, (view) => ({
        ...withTurn(view, turn.id, (existing) => ({
          ...existing,
          status: turn.status,
          error: turn.error,
          // ling's completion lists only some of the turn's items (the answer); every item streamed
          // during the turn stays, each one the completion names is updated in place, new ones last.
          items: turn.items.reduce((items, item) => upsertItem({ ...existing, items }, item).items, existing.items),
        })),
        activeTurnId: view.activeTurnId === turn.id ? null : view.activeTurnId,
      }));
    }
    case "item/started":
    case "item/completed": {
      const { threadId, turnId, item } = notification.params;
      return withThread(state, threadId, (view) => withPreview(withTurn(view, turnId, (turn) => upsertItem(turn, item)), item));
    }
    case "item/agentMessage/delta": {
      const { threadId, turnId, itemId, delta } = notification.params;
      return withThread(state, threadId, (view) =>
        withTurn(view, turnId, (turn) =>
          editItem(turn, itemId, (item) => (item.type === "agentMessage" ? { ...item, text: item.text + delta } : item)),
        ),
      );
    }
    case "item/plan/delta": {
      const { threadId, turnId, itemId, delta } = notification.params;
      return withThread(state, threadId, (view) =>
        withTurn(view, turnId, (turn) =>
          editItem(turn, itemId, (item) => (item.type === "plan" ? { ...item, text: item.text + delta } : item)),
        ),
      );
    }
    case "item/commandExecution/outputDelta": {
      const { threadId, turnId, itemId, delta } = notification.params;
      return withThread(state, threadId, (view) =>
        withTurn(view, turnId, (turn) =>
          editItem(turn, itemId, (item) =>
            item.type === "commandExecution" ? { ...item, aggregatedOutput: (item.aggregatedOutput ?? "") + delta } : item,
          ),
        ),
      );
    }
    case "item/reasoning/textDelta": {
      const { threadId, turnId, itemId, delta, contentIndex } = notification.params;
      return withThread(state, threadId, (view) =>
        withTurn(view, turnId, (turn) =>
          editItem(turn, itemId, (item) =>
            item.type === "reasoning" ? { ...item, content: appendAt(item.content, contentIndex, delta) } : item,
          ),
        ),
      );
    }
    case "item/reasoning/summaryTextDelta": {
      const { threadId, turnId, itemId, delta, summaryIndex } = notification.params;
      return withThread(state, threadId, (view) =>
        withTurn(view, turnId, (turn) =>
          editItem(turn, itemId, (item) =>
            item.type === "reasoning" ? { ...item, summary: appendAt(item.summary, summaryIndex, delta) } : item,
          ),
        ),
      );
    }
    case "turn/diff/updated": {
      const { threadId, turnId, diff } = notification.params;
      return withThread(state, threadId, (view) => withTurn(view, turnId, (turn) => ({ ...turn, diff })));
    }
    case "thread/goal/updated": {
      const { threadId, goal } = notification.params;
      return { ...state, goals: { ...state.goals, [threadId]: goal } };
    }
    case "thread/goal/cleared":
      return { ...state, goals: { ...state.goals, [notification.params.threadId]: null } };
    case "thread/tokenUsage/updated": {
      const { threadId, tokenUsage } = notification.params;
      return withThread(state, threadId, (view) => ({ ...view, usage: tokenUsage }));
    }
    case "serverRequest/resolved": {
      const { requestId } = notification.params;
      return { ...state, requests: state.requests.filter((request) => !sameId(request.id, requestId)) };
    }
    case "error": {
      const { threadId, turnId, error, willRetry } = notification.params;
      if (willRetry) return state;
      return withThread(state, threadId, (view) => withTurn(view, turnId, (turn) => ({ ...turn, error })));
    }
    case "thread/archived":
      return removeThread(state, notification.params.threadId);
    case "thread/closed": {
      const { threadId } = notification.params;
      return withThread(state, threadId, (view) => ({ ...view, activeTurnId: null }));
    }
    default:
      return state;
  }
}

/** The thread a server request belongs to, when its parameters say. */
function requestThread(request: ServerRequest): string | null {
  const params = request.params as { threadId?: unknown; conversationId?: unknown };
  if (typeof params.threadId === "string") return params.threadId;
  if (typeof params.conversationId === "string") return params.conversationId;
  return null;
}

/** Forgets an archived thread: it leaves the lists, and the view if it was open. */
function removeThread(state: WorkState, threadId: string): WorkState {
  if (!state.threads[threadId]) return state;
  const threads = { ...state.threads };
  delete threads[threadId];
  return { ...state, threads, selected: state.selected === threadId ? null : state.selected };
}

/**
 * A thread as the server answers `thread/resume`, its times those of its last activity: resuming
 * touches the thread, and its answer says it was updated now, so opening a thread showed "now" in
 * the sidebar. The latest turn's time stands when there is one, else the time the window had.
 */
export function withLastActivity(resumed: Thread, known: Thread | null): Thread {
  const turnTimes = resumed.turns.map((turn) => turn.completedAt ?? turn.startedAt ?? 0).filter((time) => time > 0);
  const last = turnTimes.length ? Math.max(...turnTimes) : known ? known.updatedAt : resumed.updatedAt;
  if (last >= resumed.updatedAt) return resumed;
  return { ...resumed, updatedAt: last, recencyAt: known?.recencyAt ?? (resumed.recencyAt !== null ? Math.min(resumed.recencyAt, last) : null) };
}

export function reduce(state: WorkState, action: Action): WorkState {
  switch (action.type) {
    case "server":
      return {
        ...state,
        server: action.status,
        requests: action.status === "exited" ? [] : state.requests,
        // Start again worked: the notices of the exit are no longer true (they stayed until a restart).
        notices: action.status === "ready" && state.notices.some((message) => message.startsWith(SERVER_EXITED))
          ? state.notices.filter((message) => !message.startsWith(SERVER_EXITED))
          : state.notices,
      };
    case "stderr":
      return { ...state, stderr: [...state.stderr, action.line].slice(-STDERR_KEPT) };
    case "protocolError":
      return { ...state, protocolErrors: [...state.protocolErrors, action.line].slice(-STDERR_KEPT) };
    case "threads": {
      const threads = { ...state.threads };
      for (const thread of action.list) {
        // A listed thread keeps what this window already streamed into it.
        // So do its title and first question, when the listing has none yet.
        const known = threads[thread.id]?.thread;
        threads[thread.id] = known
          ? { ...threads[thread.id], thread: { ...thread, turns: known.turns, preview: thread.preview || known.preview, name: thread.name ?? known.name } }
          : emptyView(thread);
      }
      return { ...state, threads };
    }
    case "opened": {
      const thread = withLastActivity(action.thread, state.threads[action.thread.id]?.thread ?? null);
      return {
        ...state,
        threads: { ...state.threads, [thread.id]: { ...emptyView(thread), usage: state.threads[thread.id]?.usage ?? null } },
        selected: thread.id,
      };
    }
    case "select":
      return { ...state, selected: action.threadId };
    case "notification":
      return onNotification(state, action.notification);
    case "serverRequest":
      return {
        ...state,
        requests: [
          ...state.requests.filter((request) => !sameId(request.id, action.request.id)),
          { id: action.request.id, method: action.request.method, params: action.request.params, threadId: requestThread(action.request) },
        ],
      };
    case "answered":
      return { ...state, requests: state.requests.filter((request) => !sameId(request.id, action.id)) };
    case "archived":
      return removeThread(state, action.threadId);
    case "notice":
      // The same notice again moves to the end rather than stacking up.
      return { ...state, notices: [...state.notices.filter((message) => message !== action.message), action.message].slice(-5) };
    case "dropNotice":
      return state.notices.includes(action.message) ? { ...state, notices: state.notices.filter((message) => message !== action.message) } : state;
    case "dismissNotice":
      return { ...state, notices: state.notices.filter((_, index) => index !== action.index) };
    case "queue":
      return { ...state, queues: { ...state.queues, [action.threadId]: action.items } };
    case "goal":
      return { ...state, goals: { ...state.goals, [action.threadId]: action.goal } };
  }
}

/**
 * The sidebar: threads grouped by project folder, most recently updated first (§6.1). Ask threads
 * have a list of their own (ask.ts), not one "project" per scratch folder.
 */
export function projects(state: WorkState, askRoot: string | null = null, folderOf: (thread: Thread) => string = (thread) => thread.cwd): { cwd: string; threads: Thread[] }[] {
  const byCwd = new Map<string, Thread[]>();
  for (const view of Object.values(state.threads)) {
    if (view.thread.ephemeral || view.thread.parentThreadId || isAskThread(view.thread, askRoot)) continue;
    // A thread in a managed worktree is listed under the project it was started for (DESKTOP_PARITY §2.6).
    const folder = folderOf(view.thread);
    const list = byCwd.get(folder) ?? [];
    list.push(view.thread);
    byCwd.set(folder, list);
  }
  const recency = (thread: Thread) => thread.recencyAt ?? thread.updatedAt;
  return [...byCwd.entries()]
    .map(([cwd, threads]) => ({ cwd, threads: threads.sort((a, b) => recency(b) - recency(a)) }))
    .sort((a, b) => recency(b.threads[0]) - recency(a.threads[0]));
}

export interface PermissionChoice {
  id: string;
  label: string;
  disabled: boolean;
  reason: string | null;
}

const PROFILE_LABELS: Record<string, string> = {
  ":read-only": "Read only",
  ":workspace": "Workspace",
  ":danger-full-access": "Full Access",
};

export const FULL_ACCESS_AT_ON =
  "airgapped is on, and Full Access runs commands with no sandbox, which is what takes their network away";

/**
 * The permission picker's rows (§8.2). At `/airgapped on` Full Access is shown disabled with the
 * reason. That is a courtesy: the refusal that binds is the server's.
 */
export function permissionChoices(profiles: PermissionProfileSummary[], airgappedOn: boolean): PermissionChoice[] {
  return profiles.map((profile) => {
    const fullAccess = profile.id === ":danger-full-access";
    const reason = !profile.allowed
      ? "not allowed by this machine's requirements"
      : airgappedOn && fullAccess
        ? FULL_ACCESS_AT_ON
        : null;
    return { id: profile.id, label: PROFILE_LABELS[profile.id] ?? profile.id, disabled: reason !== null, reason };
  });
}

/** The context indicator: how full the window is, from the last request's usage (§6.2). */
export function contextUse(usage: ThreadTokenUsage | null): { used: number; window: number; percent: number } | null {
  if (!usage || !usage.modelContextWindow) return null;
  const used = usage.last.totalTokens;
  return { used, window: usage.modelContextWindow, percent: Math.min(100, Math.round((used / usage.modelContextWindow) * 100)) };
}

/** A thread's subagents (threads it started), newest first. */
export function subagents(state: WorkState, parentId: string): Thread[] {
  return Object.values(state.threads).map((view) => view.thread).filter((thread) => thread.parentThreadId === parentId)
    .sort((a, b) => b.createdAt - a.createdAt);
}

/** Whether a thread is working now: a turn running, or the server says it is active. */
export function isWorking(state: WorkState, threadId: string): boolean {
  const view = state.threads[threadId];
  return Boolean(view?.activeTurnId) || view?.thread.status.type === "active";
}

/** How many threads are working at once: they all share the one model server (DESKTOP_PARITY §2.13). */
export function workingCount(state: WorkState): number {
  return Object.keys(state.threads).filter((id) => isWorking(state, id)).length;
}
