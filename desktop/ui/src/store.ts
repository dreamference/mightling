// What the Work window shows, as a pure function of what the server said
// (specs/DREAMFERENCE_PUFFIN_DESKTOP.md §6.1–§6.3). No I/O here: App.tsx feeds the reducer the
// bridge's events and the answers to its requests, and the tests feed it a scripted session.

import type { RequestId } from "./protocol/RequestId";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { ServerRequest } from "./protocol/ServerRequest";
import type { PermissionProfileSummary } from "./protocol/v2/PermissionProfileSummary";
import type { Thread } from "./protocol/v2/Thread";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import type { ThreadTokenUsage } from "./protocol/v2/ThreadTokenUsage";
import type { Turn } from "./protocol/v2/Turn";
import type { TurnError } from "./protocol/v2/TurnError";
import type { TurnStatus } from "./protocol/v2/TurnStatus";

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
}

export const initialState: WorkState = {
  server: "stopped",
  stderr: [],
  protocolErrors: [],
  threads: {},
  selected: null,
  requests: [],
  notices: [],
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
  | { type: "notice"; message: string }
  | { type: "dismissNotice"; index: number };

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

function onNotification(state: WorkState, notification: ServerNotification): WorkState {
  switch (notification.method) {
    case "thread/started": {
      const thread = notification.params.thread;
      if (state.threads[thread.id]) return state;
      return { ...state, threads: { ...state.threads, [thread.id]: emptyView(thread) } };
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
          // A completion may carry no items; the streamed ones stay.
          items: turn.items.length > 0 ? [...turn.items] : existing.items,
        })),
        activeTurnId: view.activeTurnId === turn.id ? null : view.activeTurnId,
      }));
    }
    case "item/started":
    case "item/completed": {
      const { threadId, turnId, item } = notification.params;
      return withThread(state, threadId, (view) => withTurn(view, turnId, (turn) => upsertItem(turn, item)));
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

export function reduce(state: WorkState, action: Action): WorkState {
  switch (action.type) {
    case "server":
      return { ...state, server: action.status, requests: action.status === "exited" ? [] : state.requests };
    case "stderr":
      return { ...state, stderr: [...state.stderr, action.line].slice(-STDERR_KEPT) };
    case "protocolError":
      return { ...state, protocolErrors: [...state.protocolErrors, action.line].slice(-STDERR_KEPT) };
    case "threads": {
      const threads = { ...state.threads };
      for (const thread of action.list) {
        // A listed thread keeps what this window already streamed into it.
        threads[thread.id] = threads[thread.id]
          ? { ...threads[thread.id], thread: { ...thread, turns: threads[thread.id].thread.turns } }
          : emptyView(thread);
      }
      return { ...state, threads };
    }
    case "opened":
      return {
        ...state,
        threads: { ...state.threads, [action.thread.id]: { ...emptyView(action.thread), usage: state.threads[action.thread.id]?.usage ?? null } },
        selected: action.thread.id,
      };
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
    case "notice":
      return { ...state, notices: [...state.notices, action.message].slice(-5) };
    case "dismissNotice":
      return { ...state, notices: state.notices.filter((_, index) => index !== action.index) };
  }
}

/** The sidebar: threads grouped by project folder, most recently updated first (§6.1). */
export function projects(state: WorkState): { cwd: string; threads: Thread[] }[] {
  const byCwd = new Map<string, Thread[]>();
  for (const view of Object.values(state.threads)) {
    if (view.thread.ephemeral || view.thread.parentThreadId) continue;
    const list = byCwd.get(view.thread.cwd) ?? [];
    list.push(view.thread);
    byCwd.set(view.thread.cwd, list);
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
