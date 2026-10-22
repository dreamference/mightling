// The page's side of several windows (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §13): what
// a window shows from its address (a full window, a thread popped out, a detached pane), and who
// holds a thread's composer, from what the main process tells each window (`windows`). The main
// process enforces it (desktop/electron/src/windows.ts): this only decides what the page offers.
// In a browser on `ling web` there is one page and everything is its own. Pure.

/** A pane that detaches into a window of its own. */
export type PaneKind = "terminal" | "review";

/** What the main process tells each window: itself, each shown thread's composer owner, and who answers for threads nobody shows. */
export interface WindowsState {
  self: string;
  owners: Record<string, string>;
  fallback: string | null;
}

/** A detached pane's window (`#pane=<kind>&thread=<id>`), or null for any other address. */
export function paneOf(hash: string): { kind: PaneKind; thread: string } | null {
  if (!hash.startsWith("#pane=")) return null;
  const fields = new URLSearchParams(hash.slice(1));
  const kind = fields.get("pane");
  const thread = fields.get("thread");
  return (kind === "terminal" || kind === "review") && thread ? { kind, thread } : null;
}

/**
 * Where a thread's composer is for this window: `here` (this window sends), `elsewhere` (another
 * window does, this one mirrors it read-only), or `here` too when nobody holds it yet or the page
 * is not in the app (the main process gives it to the first window that shows it).
 */
export function composerOf(state: WindowsState | null, thread: string | null): "here" | "elsewhere" {
  if (!state || thread === null) return "here";
  const owner = state.owners[thread];
  return owner === undefined || owner === state.self ? "here" : "elsewhere";
}

/**
 * Whether this window answers a server request nobody is asked about (the agent reading a terminal,
 * the time): the window holding the thread's composer, or for a thread no window shows (or a request
 * about no thread) the most recent window. Exactly one window answers; a detached pane never does.
 */
export function answersFor(state: WindowsState | null, thread: string | null, pane: boolean): boolean {
  if (pane) return false;
  if (!state) return true;
  const owner = thread === null ? undefined : state.owners[thread];
  if (owner !== undefined) return owner === state.self;
  return state.fallback === null || state.fallback === state.self;
}

/** The key a detached pane is known by in the window it came from. */
export const paneKey = (kind: PaneKind, thread: string) => `${kind}:${thread}`;
