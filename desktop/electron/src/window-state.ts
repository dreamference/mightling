// Window bounds persisted between runs, as the Codex app persists them: saved on move and resize,
// restored clamped to a display's work area, with the maximised state. The app's full windows are
// one session (`windows.json`, DESKTOP_ELECTRON §13): every one open when the app quits, or the last
// one closed, opens again at the next start where it was, on the view it showed. A thread popped
// out and a detached pane keep their last bounds per kind (`track`), and are not reopened: the
// first shows a thread the next start may not want, the second a terminal that ended with the app.

import fs from "node:fs";
import path from "node:path";
import { screen, type BrowserWindow, type Rectangle } from "electron";

export interface WindowState {
  bounds?: Rectangle;
  maximized?: boolean;
}

export function read(file: string): WindowState {
  try {
    const state = JSON.parse(fs.readFileSync(file, "utf8"));
    return typeof state === "object" && state !== null ? state : {};
  } catch {
    return {};
  }
}

/** The saved bounds, clamped to the work area of the display they are on (or the primary one), else `undefined`. */
export function clamped(bounds: Rectangle | undefined, workArea: Rectangle, min: { width: number; height: number }): Rectangle | undefined {
  if (!bounds) return undefined;
  const width = Math.max(min.width, Math.min(bounds.width, workArea.width));
  const height = Math.max(min.height, Math.min(bounds.height, workArea.height));
  const x = Math.min(Math.max(bounds.x, workArea.x), workArea.x + workArea.width - width);
  const y = Math.min(Math.max(bounds.y, workArea.y), workArea.y + workArea.height - height);
  return { x, y, width, height };
}

/** Puts `window` where `state` says, clamped to the display it was on. */
export function place(window: BrowserWindow, state: WindowState, min: { width: number; height: number }): void {
  const area = state.bounds ? screen.getDisplayMatching(state.bounds).workArea : screen.getPrimaryDisplay().workArea;
  const bounds = clamped(state.bounds, area, min);
  if (bounds) window.setBounds(bounds);
  if (state.maximized) window.maximize();
}

/** Calls `changed` with the window's normal bounds and maximised state after each move or resize settles. */
export function follow(window: BrowserWindow, changed: (state: WindowState) => void): void {
  let last: Rectangle | undefined;
  let timer: NodeJS.Timeout | null = null;
  const save = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      if (window.isDestroyed()) return;
      if (!window.isMaximized()) last = window.getNormalBounds();
      changed({ bounds: last ?? window.getNormalBounds(), maximized: window.isMaximized() });
    }, 300);
  };
  window.on("resize", save);
  window.on("move", save);
  window.on("maximize", save);
  window.on("unmaximize", save);
}

function write(file: string, value: unknown): void {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(value));
  } catch {
    // Not worth a dialog.
  }
}

/** Restores `window` from `file` and keeps the file up to date while it lives. */
export function track(window: BrowserWindow, file: string, min: { width: number; height: number }): void {
  place(window, read(file), min);
  follow(window, (state) => write(file, state));
}

/** One full window of the session: its view, where it was, and whether it was maximised. */
export interface SavedWindow extends WindowState {
  view: "ask" | "work";
}

/** The most windows reopened at a start. */
export const MAX_RESTORED = 8;

const isRectangle = (value: unknown): value is Rectangle =>
  typeof value === "object" && value !== null && ["x", "y", "width", "height"].every((key) => Number.isFinite((value as Record<string, unknown>)[key]));

/** The windows a session file holds, least recently used first, the malformed ones dropped. */
export function savedWindows(value: unknown): SavedWindow[] {
  const list = typeof value === "object" && value !== null && Array.isArray((value as { windows?: unknown }).windows) ? (value as { windows: unknown[] }).windows : [];
  const kept: SavedWindow[] = [];
  for (const entry of list) {
    if (typeof entry !== "object" || entry === null) continue;
    const { view, bounds, maximized } = entry as Record<string, unknown>;
    if (view !== "ask" && view !== "work") continue;
    kept.push({ view, ...(isRectangle(bounds) ? { bounds: { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height } } : {}), ...(maximized === true ? { maximized: true } : {}) });
  }
  return kept.slice(-MAX_RESTORED);
}

/** Where a new window opens beside `from`: down and to the right, back to the top-left corner of the area when it would leave it. */
export function cascade(from: Rectangle, area: Rectangle): Rectangle {
  const step = 32;
  const x = from.x + step + from.width > area.x + area.width ? area.x : from.x + step;
  const y = from.y + step + from.height > area.y + area.height ? area.y : from.y + step;
  return { x, y, width: Math.min(from.width, area.width), height: Math.min(from.height, area.height) };
}

/**
 * The full windows of a run, kept in `file`: each window's view and bounds as they change, in the
 * order they were last focused. A window closed while others stay open leaves the session; the
 * last one closed, and every one open at a quit (`freeze`), stays in it for the next start.
 */
export class WindowSession {
  private readonly windows = new Map<string, SavedWindow & { focused: number }>();
  /** How to read each live window's bounds now: a move made while it was hidden sends no event. */
  private readonly measures = new Map<string, () => WindowState>();
  /** Windows kept after they closed (the last one): dropped as soon as another opens. */
  private readonly closed = new Set<string>();
  private frozen = false;
  /** Whether this run changed the session at all: one that opened no tracked window (the audit's) never writes it. */
  private touched = false;
  private clock = 0;
  private timer: NodeJS.Timeout | null = null;

  /** `legacy` is the single window's state file of earlier versions, read when there is no session yet. */
  constructor(private readonly file: string, private readonly legacy: string | null = null) {}

  /** The windows to open at a start, least recently used first. */
  restored(): SavedWindow[] {
    try {
      return savedWindows(JSON.parse(fs.readFileSync(this.file, "utf8")));
    } catch {
      if (!this.legacy) return [];
      const state = read(this.legacy);
      return state.bounds ? [{ view: "ask", bounds: state.bounds, ...(state.maximized ? { maximized: true } : {}) }] : [];
    }
  }

  add(id: string, window: SavedWindow, measure: (() => WindowState) | null = null): void {
    if (this.frozen) return;
    this.touched = true;
    for (const gone of this.closed) this.windows.delete(gone);
    this.closed.clear();
    this.windows.set(id, { ...window, focused: ++this.clock });
    if (measure) this.measures.set(id, measure);
    this.save();
  }

  update(id: string, change: Partial<SavedWindow>): void {
    const window = this.windows.get(id);
    if (this.frozen || !window || this.closed.has(id)) return;
    this.windows.set(id, { ...window, ...change });
    this.save();
  }

  focused(id: string): void {
    this.update(id, { focused: ++this.clock } as Partial<SavedWindow>);
  }

  /** A window is closing; `keep` when it is the last one, so the next start opens it again. */
  closing(id: string, keep: boolean): void {
    if (this.frozen) return;
    this.measure(id);
    this.measures.delete(id);
    if (keep) this.closed.add(id);
    else this.windows.delete(id);
    this.save();
  }

  private measure(id: string): void {
    const window = this.windows.get(id);
    const measure = this.measures.get(id);
    if (!window || !measure) return;
    try {
      const state = measure();
      this.windows.set(id, { ...window, ...(state.bounds ? { bounds: state.bounds } : {}), maximized: state.maximized === true });
    } catch {
      // Gone already: what was last seen stays.
    }
  }

  /** The app is quitting: the windows open now are the session; the closes that follow change nothing. */
  freeze(): void {
    if (this.frozen) return;
    this.flush();
    this.frozen = true;
  }

  /** What would be written now (for the tests). */
  snapshot(): SavedWindow[] {
    return [...this.windows.values()].sort((a, b) => a.focused - b.focused).slice(-MAX_RESTORED)
      .map(({ view, bounds, maximized }) => ({ view, ...(bounds ? { bounds } : {}), ...(maximized ? { maximized: true } : {}) }));
  }

  /** Writes the session now. */
  flush(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    for (const id of this.measures.keys()) this.measure(id);
    if (!this.frozen && this.touched) write(this.file, { windows: this.snapshot() });
  }

  private save(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), 300);
  }
}
