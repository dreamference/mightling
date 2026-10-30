// Quick Entry (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY_CLAUDE.md §2.9, §4.2): a question from
// anywhere. A system-wide shortcut (Settings › General, off by default) or the tray's "New
// question…" opens a small box above the other windows; Enter sends it as a new Ask question in the
// app window, which comes forward with the answer streaming; Esc or a click elsewhere puts the box
// away. The box is the app's page at `#quick` on the same preload and channel as the pet, and may
// ask for four things only: its recent questions, one opened, a question sent, and itself hidden
// (main.ts `handleQuick`). The shortcut is Electron's `globalShortcut`: X11,
// macOS and Windows; on a Wayland session it registers nothing, which Settings says. The box lists
// the most recent Ask questions, each a click away (DESKTOP §16 B24), learnt from the app-server's
// messages as the main process routes them (`RecentQuestions`).

import path from "node:path";

import { BrowserWindow, app, screen } from "electron";

import { WORK_URL } from "./app-protocol";
import { themeArgument, type Theme } from "./theme";

/** The box's size: one composer, a line of hints. */
export const QUICK_SIZE = { width: 640, height: 168 };

/** The shortcut until Settings › General names another (Alt+Space is the window menu on most Linux desktops). */
export const DEFAULT_SHORTCUT = "Ctrl+Alt+Space";

/** The box's size with `recent` questions listed under it (DESKTOP §16 B24): 28 px a line, and the rule above them. */
export function quickSize(recent: number): { width: number; height: number } {
  const lines = Math.max(0, Math.min(recent, RECENT_SHOWN));
  return { width: QUICK_SIZE.width, height: QUICK_SIZE.height + (lines ? 8 + lines * 28 : 0) };
}

/** The longest question the box passes on (the pet's limit). */
export const QUICK_MAX = 4000;

// Ctrl is the keymap's Cmd on a Mac (keymap.ts `commandIsCtrl`), so it is CommandOrControl here.
const MODIFIERS: Record<string, string> = { ctrl: "CommandOrControl", control: "CommandOrControl", alt: "Alt", option: "Alt", shift: "Shift", super: "Super", meta: "Super", cmd: "CommandOrControl", command: "CommandOrControl", cmdorctrl: "CommandOrControl", commandorcontrol: "CommandOrControl" };
const NAMED = new Set(["Space", "Tab", "Enter", "Return", "Backspace", "Delete", "Insert", "Home", "End", "PageUp", "PageDown", "Up", "Down", "Left", "Right", "Escape", "Plus"]);

/**
 * The Electron accelerator for a shortcut in the keymap's spelling (`Ctrl+Alt+Space`), or null when
 * it is not one a system-wide shortcut may be: at least one of Ctrl, Alt or Super (Shift alone would
 * take a typed letter from every other app), and one key: a letter, a digit, F1–F24 or a named key.
 */
export function accelerator(keys: unknown): string | null {
  if (typeof keys !== "string" || keys.length > 64) return null;
  const parts = keys.split("+").map((part) => part.trim()).filter(Boolean);
  if (parts.length < 2) return null;
  const key = parts[parts.length - 1];
  const modifiers: string[] = [];
  for (const part of parts.slice(0, -1)) {
    const modifier = MODIFIERS[part.toLowerCase()];
    if (!modifier || modifiers.includes(modifier)) return null;
    modifiers.push(modifier);
  }
  if (!modifiers.some((modifier) => modifier !== "Shift")) return null;
  const named = [...NAMED].find((name) => name.toLowerCase() === key.toLowerCase());
  const normal = /^[A-Za-z0-9]$/.test(key) ? key.toUpperCase() : /^F([1-9]|1[0-9]|2[0-4])$/i.test(key) ? key.toUpperCase() : named;
  return normal ? [...modifiers, normal].join("+") : null;
}

/** Whether this session can have a system-wide shortcut at all: not a Wayland one (Electron's `globalShortcut` is X11's). */
export function shortcutsWork(platform: NodeJS.Platform = process.platform, env: NodeJS.ProcessEnv = process.env): boolean {
  if (platform !== "linux") return true;
  return env.XDG_SESSION_TYPE !== "wayland" || Boolean(env.DISPLAY && !env.WAYLAND_DISPLAY);
}

/** Where the shortcut stands, for Settings › General. */
export interface QuickStatus {
  on: boolean;
  shortcut: string;
  registered: boolean;
  problem: string | null;
}

/** The question the box sends, trimmed and capped; null when there is none. */
export function quickPrompt(text: unknown): string | null {
  if (typeof text !== "string") return null;
  const trimmed = text.trim();
  return trimmed ? trimmed.slice(0, QUICK_MAX) : null;
}

/** The box's window: frameless, above the others, not in the task bar, in the upper third of the screen the pointer is on. */
export function openQuick(preload: string, theme: Theme): BrowserWindow {
  const window = new BrowserWindow({
    ...QUICK_SIZE,
    frame: false,
    resizable: false,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    title: "Mightling: a quick question",
    backgroundColor: theme === "dark" ? "#212121" : "#ffffff",
    webPreferences: {
      preload,
      additionalArguments: [themeArgument(theme)],
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      devTools: !app.isPackaged,
    },
  });
  window.setAlwaysOnTop(true, "floating");
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  void window.loadURL(`${WORK_URL}#quick`);
  return window;
}

/** Puts the box in the upper third of the display the pointer is on, centred. */
export function placeQuick(window: BrowserWindow): void {
  const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
  window.setPosition(Math.round(area.x + (area.width - QUICK_SIZE.width) / 2), Math.round(area.y + area.height / 4));
}

/** How many recent Ask questions the box lists (DESKTOP §16 B24). */
export const RECENT_SHOWN = 5;

/** One recent question, as the box shows it. */
export interface RecentQuestion {
  id: string;
  title: string;
}

/**
 * Ask's questions, most recent first (DESKTOP §16 B24): the threads whose folder is under Ask's root
 * (`$CODEX_HOME/ask`), from `thread/list` and `thread/read` answers and `thread/started`, renamed by
 * `thread/name/updated`, moved up by `turn/started`, dropped when archived or deleted. Ephemeral
 * (incognito) threads are never listed.
 */
export class RecentQuestions {
  private readonly questions = new Map<string, { title: string; at: number }>();

  constructor(private readonly askRoot: () => string | null) {}

  private inAsk(cwd: unknown): boolean {
    const root = this.askRoot();
    if (!root || typeof cwd !== "string" || !cwd) return false;
    const folder = path.resolve(cwd);
    return folder.startsWith(path.resolve(root) + path.sep);
  }

  private learn(thread: unknown): void {
    if (typeof thread !== "object" || thread === null) return;
    const { id, cwd, name, preview, updatedAt, ephemeral } = thread as Record<string, unknown>;
    if (typeof id !== "string" || ephemeral === true || !this.inAsk(cwd)) return;
    const label = typeof name === "string" && name.trim() ? name : typeof preview === "string" ? preview : "";
    const title = label.replace(/\s+/g, " ").trim().slice(0, 120);
    if (!title) return;
    const at = typeof updatedAt === "number" ? updatedAt : Date.now() / 1000;
    const known = this.questions.get(id);
    this.questions.set(id, { title, at: Math.max(at, known?.at ?? 0) });
  }

  /** Takes one message from the server. */
  observe(message: unknown): void {
    if (typeof message !== "object" || message === null) return;
    const { method, params, result } = message as { method?: unknown; params?: Record<string, unknown>; result?: Record<string, unknown> };
    if (result && typeof result === "object") {
      this.learn(result.thread);
      if (Array.isArray(result.data)) for (const thread of result.data) this.learn(thread);
    }
    const thread = typeof params?.threadId === "string" ? params.threadId : null;
    if (method === "thread/started") this.learn(params?.thread);
    else if (method === "thread/name/updated" && thread && typeof params?.threadName === "string") {
      const known = this.questions.get(thread);
      const title = params.threadName.replace(/\s+/g, " ").trim().slice(0, 120);
      if (known && title) known.title = title;
    } else if (method === "turn/started" && thread) {
      const known = this.questions.get(thread);
      if (known) known.at = Date.now() / 1000;
    } else if ((method === "thread/archived" || method === "thread/deleted") && thread) this.questions.delete(thread);
  }

  /** The most recent questions, newest first. */
  list(): RecentQuestion[] {
    return [...this.questions.entries()].sort((a, b) => b[1].at - a[1].at).slice(0, RECENT_SHOWN).map(([id, { title }]) => ({ id, title }));
  }

  /** Whether the box may open this thread: only one it listed. */
  listed(id: unknown): boolean {
    return typeof id === "string" && this.list().some((question) => question.id === id);
  }

  reset(): void {
    this.questions.clear();
  }
}
