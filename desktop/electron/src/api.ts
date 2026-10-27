// What crosses between Work's page and the main process, over the one channel the Codex app
// uses: `window.electronBridge.sendMessageFromView(message)` out, window `MessageEvent`s in
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3). `desktop/ui/src/bridge.ts` holds the page's
// copy of these shapes.

import type { TerminalSession } from "./terminal-mirror";
import type { PaneAction, PaneKind, WindowsState } from "./windows";

/** The channel names, as the Codex app names them: `<app>_desktop:message-from-view` and `-for-view`. */
export const CHANNEL_FROM_VIEW = "mightling_desktop:message-from-view";
export const CHANNEL_FOR_VIEW = "mightling_desktop:message-for-view";
export const CHANNEL_CHUNK_ACK = "mightling_desktop:chunk-ack";

/**
 * A refusal crosses back as `{ [FAILED]: message }`, and the preload throws it in the page: a handler
 * that threw would also reach the page, but Electron logs every such throw in the main process as
 * "Error occurred in handler", which a refusal (a stopped server, a path outside a repository) is not.
 */
export const FAILED = "__mightlingFailed";

/** The refusal in an answer, or null for an answer. */
export function failure(answer: unknown): string | null {
  return answer !== null && typeof answer === "object" && typeof (answer as Record<string, unknown>)[FAILED] === "string" ? ((answer as Record<string, string>)[FAILED]) : null;
}

/** Payloads bigger than this cross in pieces, each acknowledged before the next (the Codex app does the same). */
export const CHUNK_BYTES = 1 << 20;

/** What the page sends. `work/send` carries one protocol message for `ling app-server`. */
export type FromView =
  | { type: "work/start" }
  | { type: "work/send"; message: unknown }
  | { type: "work/stop" }
  | { type: "work/target" }
  | { type: "work/airgapped"; thread: string | null }
  /** `/airgapped default <level>` from the composer: the level new threads start at. */
  | { type: "work/airgapped-default"; level: "off" | "on" }
  /** How terse answers are (cavemode.ts): the level in force for a thread or a new session, and a change. */
  | { type: "cave/status"; thread: string | null }
  | { type: "cave/set"; level: string; thread: string | null }
  /** An attachment for an Ask thread, written into its folder (ask.ts); the bytes base64. */
  | { type: "ask/upload"; thread: string; name: string; kind: "image" | "file"; data: string }
  /** An incognito question (DESKTOP_PARITY_CLAUDE §4.3): its folder marked when it starts, deleted with its files when it is left. */
  | { type: "ask/incognito"; thread: string }
  | { type: "ask/forget"; thread: string }
  /** Earlier questions for the agent (recall.ts): what is kept, keeping one, dropping the rest. */
  | { type: "recall/index" }
  | { type: "recall/write"; thread: string; updatedAt: number; markdown: string }
  | { type: "recall/prune"; keep: string[] }
  /** The microphone's recording, for text (voice.ts); the bytes base64. */
  | { type: "voice/transcribe"; mime: string; data: string }
  | { type: "context-menu"; x: number; y: number; editable: boolean; selection: string }
  /** Window-side git (git.ts): the review pane, commit, push and the pull-request page. */
  | { type: "git/repos"; cwd: string }
  | { type: "git/missing" }
  | { type: "git/status"; root: string }
  | { type: "git/diff"; root: string; scope: "unstaged" | "staged" | "commit" | "branch"; commit?: string | null }
  | { type: "git/log"; root: string }
  | { type: "git/stage" | "git/unstage" | "git/discard"; root: string; paths: string[] }
  | { type: "git/apply"; root: string; patch: string; cached?: boolean; reverse?: boolean; index?: boolean; zeroContext?: boolean }
  | { type: "git/commit"; root: string; message: string; all: boolean }
  | { type: "git/push"; root: string; force: boolean; thread: string | null }
  | { type: "git/pull-request"; root: string; thread: string | null }
  /** Managed worktrees and hand-off (worktree.ts); `root` is the worktree root from the agent's config. */
  | { type: "worktree/create"; sourceCwd: string; base: string | null; withChanges: boolean; root: string | null }
  | { type: "worktree/bind"; checkout: string; thread: string }
  | { type: "worktree/list"; sourceCwd: string; root: string | null }
  | { type: "worktree/remove"; sourceCwd: string; checkout: string; root: string | null; snapshot: string | null }
  | { type: "worktree/restore"; sourceCwd: string; snapshot: string; root: string | null }
  | { type: "worktree/branch"; checkout: string; name: string }
  | { type: "worktree/handoff"; direction: "toLocal" | "toWorktree"; worktree: string; local: string; root: string | null; thread: string }
  | { type: "git/branches"; root: string }
  /** Local environments (environments.ts): read from .codex/ and .mightling/, written to .mightling/ only. */
  | { type: "env/list"; project: string }
  | { type: "env/write"; project: string; environment: unknown }
  /**
   * The bottom panel's tasks (tasks.ts): a project environment's setup or one of its actions, named
   * by the environment's file and the action's index; the script is read from the project here,
   * never taken from the page. Refused at /airgapped on.
   */
  | { type: "task/run"; project: string; file: string; action: number | "setup"; cwd: string; thread: string | null }
  | { type: "task/stop"; id: string }
  /** The chat header's Open: a terminal or the file manager in the thread's folder. */
  | { type: "open/terminal"; dir: string }
  | { type: "open/folder"; dir: string }
  /** A citation of the user's documents (`documents/42`), opened with the system's handler (docs.ts). */
  | { type: "docs/open"; doc: string }
  /** Settings › Your documents: the collections' state, and adding, removing or rebuilding one (docs.ts). */
  | { type: "docs/status" }
  | { type: "docs/add"; folder: string }
  | { type: "docs/remove"; name: string }
  | { type: "docs/reindex"; name: string }
  | { type: "open/editor"; dir: string; editor: string | null }
  /** The editors found on this machine (editors.ts), and opening a file in one at a line. */
  | { type: "editors/list" }
  | { type: "editor/open"; root: string; path: string; line: number | null; editor: string | null }
  /** A file's size before a file tab asks the server for it whole (DESKTOP_PARITY §2.8); null when there is no such file. */
  | { type: "file/size"; path: string }
  /** The window's own preferences (prefs.ts): read whole, or some keys written. */
  | { type: "prefs/read" }
  | { type: "prefs/write"; values: Record<string, unknown> }
  /** Quick Entry's shortcut: on or off, registered or why not (quick.ts), for Settings › General. */
  | { type: "quick/status" }
  /** A link in an answer, opened in the system's browser after the page asked (links.ts); refused at /airgapped on. */
  | { type: "open/url"; url: string; thread: string | null }
  | { type: "window/minimize" }
  | { type: "window/maximize" }
  | { type: "window/close" }
  /** Shortcuts the page handles for the window (DESKTOP_PARITY §2.19): F11, Ctrl+Q, Ctrl+O. */
  | { type: "window/fullscreen" }
  | { type: "app/quit" }
  /** A thread in a window of its own, and a window kept above the others (DESKTOP_PARITY §2.1). */
  | { type: "window/popout"; thread: string }
  | { type: "window/always-on-top"; on: boolean }
  /** Several windows (windows.ts, DESKTOP_ELECTRON §13): New Window, on a view or the sender's. */
  | { type: "window/new"; view?: "ask" | "work" }
  /** Who owns which thread's composer, for this window. */
  | { type: "window/state" }
  /** The page shows a thread (or none) on a view: the composer's owner and the session follow. */
  | { type: "window/showing"; view: "ask" | "work"; thread: string | null }
  /** "Use the composer here": the thread this window shows takes its composer from the other window. */
  | { type: "window/take"; thread: string }
  /** Bring the window whose composer a thread is forward. */
  | { type: "window/show-thread"; thread: string }
  /** A thread's terminal or diff into a window of its own, and back. */
  | { type: "window/detach"; pane: PaneKind; thread: string }
  | { type: "window/dock"; pane: PaneKind; thread: string }
  /** A detached pane's request for the window it came from (windows.ts `paneAction`). */
  | { type: "pane/relay"; action: unknown }
  /** The terminal tabs every window sees (terminal-mirror.ts): the list, a tab started or ended, one closed, a tab's recent output. */
  | { type: "terminal/list" }
  | { type: "terminal/publish"; session: unknown }
  | { type: "terminal/forget"; processId: string }
  | { type: "terminal/backlog"; processId: string }
  /** The user's home folder: where a thread with no project works (Ctrl+Alt+O). */
  | { type: "app/home" }
  /** Settings › Profile and Personalization (insights.ts): statistics from the session files; `$CODEX_HOME/AGENTS.md`. */
  | { type: "insights" }
  /** Settings › Notifications: one notification now, to see that they arrive. */
  | { type: "notify/test" }
  /** Settings › Node (nodes.ts): the nodes on the network, by name, and the choice of one. */
  | { type: "node/list" }
  | { type: "node/use"; choice: string }
  /** Quit and start again, so the agent's server resolves the node chosen (Settings › Node). */
  | { type: "app/relaunch" }
  /** The Scheduled view over Night Shift (night.ts). */
  | { type: "night/list" }
  | { type: "night/report"; name: string }
  | { type: "night/add"; repo: string; task: string; test: string | null; on?: string | null; bestOf?: number | null }
  /** Whether the timer is on and its window (DESKTOP §16 B5), read from its unit file. */
  | { type: "night/status" }
  | { type: "night/drop"; repo: string; id: string }
  | { type: "night/run" }
  /** Repeating tasks (night.ts): through ling-admin, which runs where Night Shift does. */
  | { type: "night/every-add"; repo: string; task: string; test: string | null; cadence: "daily" | "weekly"; weekday: string | null }
  | { type: "night/every-list" }
  | { type: "night/every-drop"; id: string }
  /** A finished task's last message, diff and transcript (night.ts `taskDetails`). */
  | { type: "night/details"; id: string }
  | { type: "personal/read" }
  | { type: "personal/write"; text: string }
  | { type: "dialog/open-folder" }
  /** A thread's export (desktop/ui/src/exportthread.ts): a save dialog, then the text written there. */
  | { type: "dialog/save-text"; name: string; text: string }
  /** A project's knowledge files (knowledge.ts), by the project's server id; add opens a file dialog. */
  | { type: "project/knowledge-list"; projectId: string }
  | { type: "project/knowledge-add"; projectId: string }
  | { type: "project/knowledge-remove"; projectId: string; name: string }
  /** Settings › Pets: a picture of the user's own for the pet. */
  | { type: "dialog/open-image" }
  /** The pull-request tab (prs.ts): `gh` in the repository, when Settings › Git turns it on; refused at airgapped on. */
  | { type: "pr/list"; root: string; inbox: "review" | "mine" | "open"; thread: string | null }
  | { type: "pr/view"; root: string; number: number; thread: string | null }
  | { type: "pr/diff"; root: string; number: number; thread: string | null }
  | { type: "pr/review"; root: string; number: number; verdict: "comment" | "approve" | "request-changes"; body: string; thread: string | null }
  | { type: "pr/status"; root: string }
  /** The agent message board (board.ts): its SQLite file, read only; `sqliteHome` is config.toml's `sqlite_home`, if set. */
  | { type: "board/read"; sqliteHome: string | null }
  /** The preview tab (preview.ts): a page in a view laid over the tab's body, under the rules of preview-rules.ts. */
  | { type: "preview/open"; id: string; url: string; thread: string | null }
  | { type: "preview/navigate"; id: string; url: string; thread: string | null }
  | { type: "preview/go"; id: string; step: "back" | "forward" }
  /** The agent's console tool (windowtools.ts): the recent console of the thread's previews. */
  | { type: "preview/console"; thread: string | null }
  | { type: "preview/reload"; id: string; fresh?: boolean }
  | { type: "preview/place"; id: string; bounds: { x: number; y: number; width: number; height: number }; visible: boolean }
  | { type: "preview/pick"; id: string }
  | { type: "preview/allow-once"; id: string; site: string }
  | { type: "preview/devtools"; id: string }
  | { type: "preview/close"; id: string }
  | { type: "preview/clear" };

/** What the page hears, re-dispatched by the preload as `MessageEvent`s whose `data` is one of these. */
export type ForView =
  | { channel: "work://message"; payload: unknown }
  | { channel: "work://stderr"; payload: string }
  | { channel: "work://protocol-error"; payload: string }
  | { channel: "work://exit"; payload: number | null }
  /** A link (`ling://…`) or a second `ling app` asked for something: the app window shows it. */
  | { channel: "link"; payload: { cwd: string | null; thread: string | null; prompt?: string | null; page?: string; plugin?: string; send?: boolean } }
  /** Quick Entry's box was asked for (quick.ts): it clears and takes the focus. */
  | { channel: "quick://open"; payload: null }
  /** A bottom-panel task started, wrote output, or ended (tasks.ts). */
  | { channel: "task://event"; payload: { id: string; kind: "started" | "output" | "exit"; label?: string; cwd?: string; data?: string; code?: number | null } }
  /** A preview tab's page moved, loaded, or asked for a site the rules hold back (preview.ts). */
  | { channel: "preview://event"; payload: { id: string; kind: "state" | "ask" | "blocked" | "download" | "key"; key?: string; url?: string; title?: string; loading?: boolean; canGoBack?: boolean; canGoForward?: boolean; site?: string; reason?: string; error?: string | null } }
  /** The pet's state (pet.ts), for the pet's own window. */
  /** The theme the window shows now (theme.ts): the preference, or for System the system's. */
  | { channel: "theme"; payload: "light" | "dark" }
  | { channel: "pet://state"; payload: { working: number; waiting: number; finished: number; size: "small" | "medium" | "large"; image: string | null } }
  /** Show Ask or Work: the menu, a second instance or a link asked for one. */
  | { channel: "view"; payload: "ask" | "work" }
  /** Who owns which thread's composer (windows.ts), sent to each window whenever it changes. */
  | { channel: "windows"; payload: WindowsState }
  /** Every terminal tab (terminal-mirror.ts), whenever the list changes. */
  | { channel: "terminals"; payload: TerminalSession[] }
  /** A pane detached from this window closed: it shows here again. */
  | { channel: "pane://docked"; payload: { pane: PaneKind; thread: string } }
  /** A pane detached from this window asks for something (windows.ts `paneAction`). */
  | { channel: "pane://action"; payload: PaneAction }
  /** The Window menu's pane items, for the page in front: detach (or dock back) the terminal or the diff. */
  | { channel: "command"; payload: "detachTerminal" | "detachReview" }
  /** Another device acted on a thread here through the sync host (sync-host.ts): "Approved from the phone". */
  | { channel: "sync://notice"; payload: { text: string; thread: string | null } };

export interface Started {
  served_model: string | null;
  started: boolean;
  /** The folder Ask threads' scratch folders live in, canonical, as `ling web` answers it too. */
  ask_root: string | null;
}

export interface Airgapped {
  level: "off" | "on";
  source: string;
}

/** `ling cavemode --json`: the level in force, where it came from, the configured one and the defaults. */
export interface CaveMode {
  level: string;
  source: string;
  configured: string | null;
  default: string;
  tuiDefault: string;
  levels: { name: string; summary: string }[];
}

/** A piece of a payload too big for one message. */
export interface Chunk {
  __chunk: { id: string; index: number; total: number; part: string };
}

export const isChunk = (value: unknown): value is Chunk =>
  typeof value === "object" && value !== null && "__chunk" in value;

/** Splits a message into chunks when it is larger than `CHUNK_BYTES`; otherwise returns it alone. */
export function chunked(message: unknown, limit = CHUNK_BYTES): unknown[] {
  const text = JSON.stringify(message);
  if (text.length <= limit) return [message];
  const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const total = Math.ceil(text.length / limit);
  return Array.from({ length: total }, (_, index) => ({ __chunk: { id, index, total, part: text.slice(index * limit, (index + 1) * limit) } }));
}

/** Reassembles chunks; returns the whole message once the last piece is in, else `undefined`. */
export class Reassembler {
  private readonly parts = new Map<string, string[]>();

  take(chunk: Chunk): unknown {
    const { id, index, total, part } = chunk.__chunk;
    const parts = this.parts.get(id) ?? new Array<string>(total);
    parts[index] = part;
    this.parts.set(id, parts);
    for (let i = 0; i < total; i++) if (parts[i] === undefined) return undefined;
    this.parts.delete(id);
    return JSON.parse(parts.join(""));
  }
}
