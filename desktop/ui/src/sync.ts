// Threads from other devices (specs/DREAMFERENCE_MIGHTLING_SYNC.md, Phases 1 and 2): on `ling web`
// with the node's sync hub on, the page lists the threads every paired device published, reads one
// as a transcript and, with `control`, archives or deletes it everywhere. Phase 2 (§18.3, §18.4)
// makes an open thread live: the turn as it streams on its device, the approvals waiting, and, with
// `control`, a message, an answer, a stop or a new thread sent back through the hub. The turn still
// runs on the device that holds the code (§0 decision 4); the hub only carries the command, and the
// device vets it with its own policy as if its user had acted there.
// Same-origin requests to `/api/sync/`, with the page's own cookie; no app-server method, so the
// bridge policy is unchanged. The desktop app has no hub of its own and shows none of this yet.

/** A thread as the hub lists it (ling-rs/web/src/sync_store.rs `ThreadView::listed`). */
export interface SyncedThread {
  thread: string;
  owner: string;
  owner_name: string;
  title: string;
  archived: boolean;
  cwd: string | null;
  source: string | null;
  created: number;
  updated: number;
  fork_of: string | null;
}

/** One entry of a transcript (`items_of`). */
export interface SyncedItem {
  kind: "user" | "agent" | "command" | "files" | "tool" | "turn";
  text: string;
  status?: string | null;
  exit_code?: number | null;
  output?: string;
  at?: string | null;
}

/** A paired device as `/api/sync/status` lists it, with its hosts' presence (sync_live.rs `Presence`). */
export interface SyncDevice {
  id: string;
  name: string;
  kind: string;
  scope: "control" | "watch";
  online: boolean;
  /** Seconds; 0 when no host of it has posted since the hub started. */
  last_seen: number;
  projects: string[];
  models: string[];
}

export interface SyncStatus {
  enabled: boolean;
  cursor: number;
  /** This machine's own sync agent: its threads are the page's own, listed by the app-server. */
  this_device: string | null;
  caller: { device: string; agent: boolean; scope: "control" | "watch" };
  devices?: SyncDevice[];
}

/** What a following device asked a device to do, and how it went (sync_live.rs `Command`). */
export interface SyncCommand {
  id: number;
  kind: "send" | "approve" | "stop" | "start";
  device: string;
  thread: string | null;
  text?: string;
  model?: string;
  request?: unknown;
  decision?: string;
  cwd?: string;
  by: string;
  at: number;
  state: "queued" | "waiting" | "delivered" | "done" | "failed" | "expired";
  error?: string | null;
}

/** A command as the page sends it (sync_live.rs `Ask`). */
export type SyncAsk =
  | { kind: "send"; thread: string; text: string; model?: string }
  | { kind: "approve"; thread: string; request: unknown; decision: "accept" | "decline" }
  | { kind: "stop"; thread: string }
  | { kind: "start"; device: string; cwd: string; text: string };

/** A server request waiting on the device, exactly as its app-server sent it. */
export interface WaitingRequest {
  id: number | string;
  method: string;
  params?: Record<string, unknown>;
}

/** One app-server message a host passed on (sync_live.rs `FOLLOWED`, and server requests). */
export interface LiveMessage {
  id?: number | string;
  method?: string;
  params?: Record<string, unknown>;
}

/** `GET /api/sync/live/<thread>` (sync_hub.rs `follow`). */
export interface LiveAnswer {
  events: { seq: number; message: LiveMessage }[];
  next: number;
  reset: boolean;
  running: boolean;
  turn: string | null;
  pending: WaitingRequest[];
  queued: SyncCommand[];
  owner: string;
  owner_name: string;
  online: boolean;
  last_seen: number;
  models: string[];
}

/** The two server requests another device may answer (sync_live.rs `ANSWERABLE`). */
export const ANSWERABLE: readonly string[] = ["item/commandExecution/requestApproval", "item/fileChange/requestApproval"];

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** The hub said no: its own words (`{"error": …}`), and the status it said them with. */
export class HubRefusal extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function json<T>(answer: Response): Promise<T> {
  const value = await answer.json().catch(() => ({}));
  if (!answer.ok) throw new HubRefusal(typeof value?.error === "string" ? value.error : `the hub answered ${answer.status}`, answer.status);
  return value as T;
}

const POST_JSON = { method: "POST", headers: { "Content-Type": "application/json" } };

/** The hub's routes, over `fetch` (a stand-in in the tests). */
export function hub(fetcher: Fetch = (input, init) => fetch(input, init)) {
  return {
    /** The hub's state, or null when it is off (every route answers 404 then) or unreachable. */
    status: async (): Promise<SyncStatus | null> => {
      const answer = await fetcher("/api/sync/status").catch(() => null);
      if (!answer || !answer.ok) return null;
      // An answer that is not JSON (a page served in the route's place) is a hub that is not there.
      return answer.json().catch(() => null) as Promise<SyncStatus | null>;
    },
    threads: () => fetcher("/api/sync/threads").then((answer) => json<{ cursor: number; this_device: string | null; threads: SyncedThread[] }>(answer)),
    read: (thread: string) => fetcher(`/api/sync/threads/${encodeURIComponent(thread)}`).then((answer) => json<{ thread: SyncedThread; items: SyncedItem[] }>(answer)),
    /** Resolves once anything changed after `cursor`, or after `wait` seconds; with the new cursor. */
    changed: (cursor: number, wait = 25) => fetcher(`/api/sync/manifest?since=${cursor}&wait=${wait}`).then((answer) => json<{ cursor: number }>(answer)).then((value) => value.cursor),
    archive: (thread: string, archived: boolean) => fetcher(`/api/sync/threads/${encodeURIComponent(thread)}/meta`, {
      ...POST_JSON, body: JSON.stringify({ archived }),
    }).then((answer) => json<unknown>(answer)),
    remove: (thread: string) => fetcher(`/api/sync/threads/${encodeURIComponent(thread)}/delete`, {
      ...POST_JSON, body: "{}",
    }).then((answer) => json<unknown>(answer)),
    /** A thread's events after `after`, held up to `wait` seconds for one; `signal` ends the wait. */
    live: (thread: string, after: number, wait = 25, signal?: AbortSignal) =>
      fetcher(`/api/sync/live/${encodeURIComponent(thread)}?after=${after}&wait=${wait}`, signal ? { signal } : undefined).then((answer) => json<LiveAnswer>(answer)),
    /** Sends a command; the hub's refusal (400, 403, 409, 429) rejects with its words. */
    command: (ask: SyncAsk) => fetcher("/api/sync/commands", { ...POST_JSON, body: JSON.stringify(ask) })
      .then((answer) => json<{ command: SyncCommand }>(answer)).then((value) => value.command),
    commandState: (id: number) => fetcher(`/api/sync/commands/${id}`).then((answer) => json<{ command: SyncCommand }>(answer)).then((value) => value.command),
  };
}

export type Hub = ReturnType<typeof hub>;

/** A message for the thread; with no model named, the device keeps the thread's own. */
export function sendAsk(thread: string, text: string, model = ""): SyncAsk {
  return model ? { kind: "send", thread, text, model } : { kind: "send", thread, text };
}

/** The answer to a waiting approval, naming it by its id exactly as the device sent it. */
export function approveAsk(thread: string, request: WaitingRequest, decision: "accept" | "decline"): SyncAsk {
  return { kind: "approve", thread, request: request.id, decision };
}

export function stopAsk(thread: string): SyncAsk {
  return { kind: "stop", thread };
}

export function startAsk(device: string, cwd: string, text: string): SyncAsk {
  return { kind: "start", device, cwd, text };
}

/** Whether this page may answer other devices' threads (`control`) or only watch them. */
export function canControl(status: SyncStatus): boolean {
  return status.caller.scope === "control";
}

/**
 * The devices a new thread can start on: online sync devices that offer folders, this one aside;
 * none for a device that may only watch.
 */
export function startDevices(status: SyncStatus): SyncDevice[] {
  if (!canControl(status)) return [];
  return (status.devices ?? []).filter((device) => device.kind === "sync" && device.online && device.projects.length > 0 && device.id !== status.this_device);
}

/** The line above a live thread: where it runs, or since when its device has been away. */
export function banner(live: Pick<LiveAnswer, "online" | "running" | "owner_name" | "owner" | "last_seen">, now = Date.now() / 1000): string {
  const name = live.owner_name || live.owner || "its device";
  if (live.online) return live.running ? `Running on ${name} · working…` : `Running on ${name}`;
  const seen = live.last_seen > 0 ? `last seen ${ago(live.last_seen, now)}` : "not seen since the hub started";
  return `${name} is offline, ${seen}; messages wait up to 24 hours`;
}

/** Where a queued command is. */
export function queuedLabel(command: SyncCommand): string {
  switch (command.state) {
    case "waiting": return "waiting for the device";
    case "queued": return "on its way";
    case "delivered": return "delivered";
    case "done": return "done";
    case "failed": return command.error || "the device refused it";
    case "expired": return "the device did not come back within 24 hours";
  }
}

/** Whether a command has gone as far as it will. */
export function finished(command: SyncCommand): boolean {
  return command.state === "done" || command.state === "failed" || command.state === "expired";
}

/**
 * Reads a command's state until it is done, failed or expired, telling `update` each change.
 * A message for an offline device may wait a day, so a waiting one is read less often.
 */
export async function awaitCommand(api: Pick<Hub, "commandState">, first: SyncCommand, update: (command: SyncCommand) => void,
  options: { signal?: AbortSignal; sleep?: (ms: number) => Promise<void> } = {}): Promise<SyncCommand> {
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  let command = first;
  while (!finished(command)) {
    await sleep(command.state === "waiting" ? 10000 : 1500);
    if (options.signal?.aborted) return command;
    try {
      const now = await api.commandState(command.id);
      if (now.state !== command.state || now.error !== command.error) update(now);
      command = now;
    } catch (error) {
      if (error instanceof HubRefusal && error.status === 404) return command;
    }
  }
  return command;
}

// The turn as it streams (§18.4). The hub's transcript is rendered from the rollout, which the
// device's agent uploads within a second, so the two overlap: while a turn is live, the transcript
// is shown up to the turn ends it had when the turn began (`base`), and the live events own what
// follows. A turn that finished stays live until the transcript has one more turn end, then the
// rollout's items take over; a turn whose end never shows is let go after a few seconds.

/** A transcript entry built from live events, keyed by the app-server's item id. */
export interface LiveItem extends SyncedItem {
  id: string;
  /** An agent message still streaming in. */
  streaming?: boolean;
}

export interface LiveTurn {
  items: LiveItem[];
  /** When `turn/completed` came (ms), or null while it runs. */
  finishedAt: number | null;
}

export interface LiveView {
  /** The transcript's turn ends before the first live turn. */
  base: number;
  turns: LiveTurn[];
  /** Files of each file change and the command of each command, by item id, for the approvals. */
  changes: Record<string, string[]>;
  commands: Record<string, string>;
  /** The last `error` notification's message. */
  error: string | null;
}

/** How long a finished turn waits for its end to reach the transcript. */
export const SETTLE_WITHIN = 5000;

/** How many turns the transcript says ended. */
export function turnEnds(transcript: SyncedItem[]): number {
  return transcript.filter((item) => item.kind === "turn").length;
}

export function emptyLive(transcript: SyncedItem[]): LiveView {
  return { base: turnEnds(transcript), turns: [], changes: {}, commands: {}, error: null };
}

const record = (value: unknown): Record<string, unknown> => (value && typeof value === "object" ? value as Record<string, unknown> : {});
const text = (value: unknown): string => (typeof value === "string" ? value : "");

function lastLines(output: string, count = 20): string {
  const lines = output.split("\n");
  if (lines.at(-1) === "") lines.pop();
  return lines.slice(-count).join("\n");
}

/** An app-server item (protocol/v2 `ThreadItem`) as a transcript entry, or null for kinds not shown. */
export function itemOf(item: Record<string, unknown>): LiveItem | null {
  const id = text(item.id);
  switch (item.type) {
    case "userMessage": {
      const content = Array.isArray(item.content) ? item.content.map(record) : [];
      return { id, kind: "user", text: content.filter((part) => part.type === "text").map((part) => text(part.text)).join("\n") };
    }
    case "agentMessage":
      return { id, kind: "agent", text: text(item.text) };
    case "commandExecution":
      return {
        id, kind: "command", text: text(item.command), status: text(item.status) || null,
        exit_code: typeof item.exitCode === "number" ? item.exitCode : null, output: lastLines(text(item.aggregatedOutput)),
      };
    case "fileChange": {
      const changes = Array.isArray(item.changes) ? item.changes.map(record) : [];
      return { id, kind: "files", text: changes.map((change) => text(change.path)).filter(Boolean).join("\n"), status: text(item.status) || null };
    }
    case "mcpToolCall":
      return { id, kind: "tool", text: `${text(item.server)}.${text(item.tool)}`, status: text(item.status) || null };
    case "webSearch":
      return { id, kind: "tool", text: `web search: ${text(item.query)}` };
    default:
      return null;
  }
}

function put(turn: LiveTurn, item: LiveItem): LiveTurn {
  const at = turn.items.findIndex((known) => known.id === item.id);
  const items = at < 0 ? [...turn.items, item] : turn.items.map((known, index) => (index === at ? item : known));
  return { ...turn, items };
}

/**
 * Folds a batch of live events into the view. `initial` is the first batch after opening the thread
 * (or after `reset`): it replays the hub's buffer, whose finished turns the transcript already has,
 * so only a turn still running is kept.
 */
export function foldLive(view: LiveView, events: { message: LiveMessage }[], now = Date.now(), initial = false): LiveView {
  let { turns, base } = view;
  const changes = { ...view.changes };
  const commands = { ...view.commands };
  let error = view.error;
  // The turn events go to: the last one, unless it finished (then a new one begins).
  const current = (): LiveTurn => {
    const last = turns.at(-1);
    if (last && last.finishedAt === null) return last;
    const fresh: LiveTurn = { items: [], finishedAt: null };
    turns = [...turns, fresh];
    return fresh;
  };
  const replace = (turn: LiveTurn) => { turns = [...turns.slice(0, -1), turn]; };
  for (const { message } of events) {
    const params = record(message.params);
    if (params.truncated !== undefined) continue;
    const item = record(params.item);
    switch (message.method) {
      case "turn/started":
        current();
        error = null;
        break;
      case "item/started":
      case "item/completed": {
        if (item.type === "fileChange") changes[text(item.id)] = (Array.isArray(item.changes) ? item.changes.map(record) : []).map((change) => text(change.path)).filter(Boolean);
        if (item.type === "commandExecution") commands[text(item.id)] = text(item.command);
        const entry = itemOf(item);
        if (!entry) break;
        const turn = current();
        // A message's start carries no words yet; its deltas bring them.
        if (message.method === "item/started") {
          if (entry.kind === "agent") entry.streaming = true;
          if (turn.items.some((known) => known.id === entry.id)) break;
        }
        replace(put(turn, entry));
        break;
      }
      case "item/agentMessage/delta": {
        const turn = current();
        const id = text(params.itemId);
        const known = turn.items.find((entry) => entry.id === id);
        const entry: LiveItem = known ? { ...known, text: known.text + text(params.delta) } : { id, kind: "agent", text: text(params.delta), streaming: true };
        replace(put(turn, entry));
        break;
      }
      case "turn/completed": {
        const last = turns.at(-1);
        if (last && last.finishedAt === null) replace({ items: last.items.map((entry) => ({ ...entry, streaming: false })), finishedAt: now });
        break;
      }
      case "error":
        error = text(record(params.error).message) || text(params.message) || "the turn failed";
        break;
    }
  }
  if (initial) {
    // Turns that finished before the page looked are in the transcript already.
    const running = turns.filter((turn) => turn.finishedAt === null);
    turns = running.length ? [running[running.length - 1]] : [];
  }
  return { base, turns, changes, commands, error };
}

/**
 * Lets the transcript take over each finished turn whose end it now has (or that waited longer than
 * `SETTLE_WITHIN` for it). With no live turn left, `base` follows the transcript.
 */
export function settleLive(view: LiveView, transcript: SyncedItem[], now = Date.now()): LiveView {
  const ends = turnEnds(transcript);
  let { base } = view;
  const turns = [...view.turns];
  while (turns.length && turns[0].finishedAt !== null && (ends > base || now - turns[0].finishedAt >= SETTLE_WITHIN)) {
    turns.shift();
    base = Math.min(base + 1, ends);
  }
  if (!turns.length) base = ends;
  if (base === view.base && turns.length === view.turns.length) return view;
  return { ...view, base, turns };
}

/** What a live thread shows: the transcript up to the live turns, then the live turns' items. */
export function liveShown(view: LiveView, transcript: SyncedItem[]): { transcript: SyncedItem[]; live: LiveItem[] } {
  const live = view.turns.flatMap((turn) => turn.items);
  if (!view.turns.length) return { transcript, live };
  let ends = 0;
  const cut = transcript.findIndex((item) => item.kind === "turn" && ++ends === view.base);
  return { transcript: view.base === 0 ? [] : cut < 0 ? transcript : transcript.slice(0, cut + 1), live };
}

/** A waiting approval as the page shows it, or null for a question answered on the device itself. */
export function approvalOf(request: WaitingRequest, view: LiveView): { kind: "command"; command: string; cwd: string; reason: string } | { kind: "files"; files: string[]; reason: string } | null {
  if (!ANSWERABLE.includes(request.method)) return null;
  const params = record(request.params);
  const reason = text(params.reason);
  const itemId = text(params.itemId);
  if (request.method === "item/commandExecution/requestApproval") {
    return { kind: "command", command: text(params.command) || view.commands[itemId] || "", cwd: text(params.cwd), reason };
  }
  return { kind: "files", files: view.changes[itemId] ?? [], reason };
}

/** Other devices' threads, grouped by device in the order of each device's newest thread. */
export function byDevice(threads: SyncedThread[], thisDevice: string | null, archived: boolean): { device: string; name: string; threads: SyncedThread[] }[] {
  const groups = new Map<string, { device: string; name: string; threads: SyncedThread[] }>();
  const shown = threads.filter((thread) => thread.owner !== thisDevice && thread.archived === archived).sort((a, b) => b.updated - a.updated);
  for (const thread of shown) {
    const group = groups.get(thread.owner) ?? { device: thread.owner, name: thread.owner_name || thread.owner, threads: [] };
    group.threads.push(thread);
    groups.set(thread.owner, group);
  }
  return [...groups.values()];
}

/** How many threads other devices have that this page would list. */
export function elsewhere(threads: SyncedThread[], thisDevice: string | null): number {
  return threads.filter((thread) => thread.owner !== thisDevice && !thread.archived).length;
}

/** "3 min ago" for a time in seconds. */
export function ago(seconds: number, now = Date.now() / 1000): string {
  const elapsed = Math.max(0, Math.round(now - seconds));
  if (elapsed < 60) return "just now";
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)} min ago`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)} h ago`;
  return `${Math.floor(elapsed / 86400)} days ago`;
}

/** The last part of a folder, for a thread's line. */
export function folderName(cwd: string | null): string {
  return cwd ? (cwd.split(/[\\/]/).filter(Boolean).pop() ?? cwd) : "";
}
