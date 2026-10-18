// The window's shell (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1, §2.19): back and forward
// through what the window showed, undo and redo of the app's own actions (archive, rename, …),
// the font size, the surfaces Ctrl+W closes and Ctrl+Shift+T reopens, and the entries of the
// command menu. Pure.

/** A place the window showed: a view, and the thread in it. */
export interface Place {
  view: "ask" | "work";
  thread: string | null;
}

const samePlace = (a: Place, b: Place) => a.view === b.view && a.thread === b.thread;

/** Back and forward (Ctrl+[ / Ctrl+], the mouse's side buttons). */
export class History {
  private places: Place[] = [];
  private index = -1;

  /** The window moved to `place`; going somewhere new drops the forward places. */
  visit(place: Place): void {
    if (this.index >= 0 && samePlace(this.places[this.index], place)) return;
    this.places = [...this.places.slice(0, this.index + 1), place].slice(-100);
    this.index = this.places.length - 1;
  }

  back(): Place | null {
    if (this.index <= 0) return null;
    this.index--;
    return this.places[this.index];
  }

  forward(): Place | null {
    if (this.index >= this.places.length - 1) return null;
    this.index++;
    return this.places[this.index];
  }

  get canGoBack(): boolean {
    return this.index > 0;
  }

  get canGoForward(): boolean {
    return this.index < this.places.length - 1;
  }
}

/** An app action that can be taken back (Ctrl+Z) and done again (Ctrl+Shift+Z). */
export interface Undoable {
  label: string;
  undo: () => Promise<void> | void;
  redo: () => Promise<void> | void;
}

export class UndoStack {
  private done: Undoable[] = [];
  private undone: Undoable[] = [];

  push(action: Undoable): void {
    this.done = [...this.done, action].slice(-50);
    this.undone = [];
  }

  async undo(): Promise<string | null> {
    const action = this.done.pop();
    if (!action) return null;
    await action.undo();
    this.undone.push(action);
    return action.label;
  }

  async redo(): Promise<string | null> {
    const action = this.undone.pop();
    if (!action) return null;
    await action.redo();
    this.done.push(action);
    return action.label;
  }

  get canUndo(): boolean {
    return this.done.length > 0;
  }

  get canRedo(): boolean {
    return this.undone.length > 0;
  }
}

/** The font scale steps (Ctrl+= / Ctrl+- / Ctrl+0), as Chromium's zoom levels go. */
export const FONT_SCALES = [0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];

export function nextScale(current: number, direction: 1 | -1 | 0): number {
  if (direction === 0) return 1;
  const index = FONT_SCALES.findIndex((scale) => scale >= current - 0.001);
  const at = index < 0 ? FONT_SCALES.length - 1 : index;
  return FONT_SCALES[Math.max(0, Math.min(FONT_SCALES.length - 1, at + direction))];
}

/** The surfaces beside the thread that Ctrl+W closes, frontmost first, and Ctrl+Shift+T reopens. */
export type Surface = "review" | "bottomPanel" | `file:${string}`;

export class ClosedSurfaces {
  private closed: Surface[] = [];

  close(surface: Surface): void {
    this.closed = [...this.closed.filter((entry) => entry !== surface), surface];
  }

  reopen(): Surface | null {
    return this.closed.pop() ?? null;
  }
}

/** An entry of the command menu (Ctrl+K): a command, a thread, a project or a settings page. */
export interface MenuEntry {
  id: string;
  kind: "command" | "thread" | "project" | "settings";
  label: string;
  detail: string;
  shortcut: string | null;
}

/** Entries matching `query`: every word of it in the label or the detail, labels that start with it first. */
export function filterEntries(entries: MenuEntry[], query: string): MenuEntry[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const matching = entries.filter((entry) => {
    const text = `${entry.label} ${entry.detail} ${entry.shortcut ?? ""}`.toLowerCase();
    return words.every((word) => text.includes(word));
  });
  const q = query.trim().toLowerCase();
  return matching.sort((a, b) => Number(!a.label.toLowerCase().startsWith(q)) - Number(!b.label.toLowerCase().startsWith(q)));
}
