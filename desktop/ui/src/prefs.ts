// The window's own preferences, as the page sees them (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md
// §2.15): in the desktop app the main process keeps them (desktop/electron/src/prefs.ts) and checks
// every write against prefs.json; in a browser they live in localStorage, which may be missing or
// refuse, so every read falls back to the defaults.

import * as bridge from "./bridge";
import schemaFile from "./prefs.json";
import type { Section } from "./threads";

export interface Prefs {
  /** What Enter does in the composer: send (Shift+Enter for a new line), or a new line (Ctrl+Enter sends). */
  enterBehaviour: "enter" | "ctrlEnter" | "multiline";
  /** A message sent while a turn runs: steer the running turn, or wait in the queue for the next one. */
  followUp: "steer" | "queue";
  /** The reasoning effort last chosen in the picker; null for the model's default. */
  effort: string | null;
  /** Models picked recently, newest first (Alt+M). */
  recentModels: string[];
  /** The editor files open in (an id of desktop/electron/src/editors.ts); null for the system's choice. */
  editor: string | null;
  /** The review pane's diffs: one column, or old and new side by side. */
  diffLayout: "unified" | "split";
  /** Threads that work in a managed worktree, by thread id (worktrees.ts). */
  worktreeThreads: Record<string, WorktreeLink>;
  /** The project context (projects.ts) each project thread was last given, by thread id: its stamp, so a thread is told again only after the project changed. */
  projectContextSent: Record<string, string>;
  /** The sidebar's projects and folders in the order the user dragged them (`project:<id>`, `folder:<path>`; projects.ts `ordered`). */
  projectOrder: string[];
  /** Whether a new Work thread may read the terminal tabs and the previews' console (windowtools.ts; the owner's decision 5, on by default). */
  agentReadsWindow: boolean;
  /** Worktrees made permanent: projects of their own, never cleaned up. */
  permanentWorktrees: string[];
  /** The text size, as a factor of the base (Ctrl+= / Ctrl+- / Ctrl+0). */
  fontScale: number;
  /** Keyboard shortcuts the user rebound: action id to its bindings (Settings › Keyboard shortcuts). */
  keyOverrides: Record<string, string[]>;
  /** Whether the sidebar is folded away (Ctrl+B). */
  sidebarHidden: boolean;
  /** Pinned threads and projects, first in the sidebar, in pin order. */
  pinnedThreads: string[];
  pinnedProjects: string[];
  /** The user's sections of the sidebar (threads.ts; window-side until threadSection/* is allowed). */
  sections: Section[];
  /** Threads that finished a turn while not shown, or were marked unread. */
  unreadThreads: string[];
  /** Whether the sidebar's Recent list is open. */
  recentsOpen: boolean;
  /** The workspace layout (§2.1): the thread beside its tabs, the tabs in full, or the thread alone with the tabs hidden. */
  layout: "split" | "tabs" | "chat";
  /** Keep the machine awake while a turn runs (Settings › General). */
  preventSleep: boolean;
  /** Open a document or an image the agent created in a tab when its turn ends (the artifacts viewer, Settings › General). */
  openArtifacts: boolean;
  /** A long paste becomes an attachment (DESKTOP_PARITY_CLAUDE §4.6); on by default. */
  pasteAsFile: boolean;
  /** The preview tab's sites outside this machine and the LAN (Settings › Browser): host to allow or block; others ask. */
  previewSites: Record<string, "allow" | "block">;
  /** Developer tools for preview tabs (Settings › Browser › Developer mode); off by default. */
  previewDevTools: boolean;
  /** The pet (Settings › Pets, Super+Alt+P): shown or not, its size, and a picture of the user's own instead of Mightling's mark. */
  petShown: boolean;
  /** The agent may search earlier Ask questions (DESKTOP_PARITY_CLAUDE §4.4): copies kept and a skill in place; off by default. */
  recallQuestions: boolean;
  /** Quick Entry (DESKTOP_PARITY_CLAUDE §4.2): a question from anywhere through a system-wide shortcut; off by default. */
  quickEntry: boolean;
  /** Its shortcut, in the keymap's spelling (`Ctrl+Alt+Space`). */
  quickEntryShortcut: string;
  /** Closing the window hides it, the app staying in the tray (so Quick Entry and running turns go on); off by default. */
  keepInTray: boolean;
  /** The first-run page was seen (DESKTOP §16 B6). */
  welcomed: boolean;
  /** Names, paths and symbols dictation should spell as written (Settings › Voice), sent to the speech-to-text service as its prompt. */
  dictationWords: string[];
  /** Work's sidebar: threads by project (the default), or every thread by date (DESKTOP §16 B10). */
  threadOrder: "projects" | "chronological";
  petSize: "small" | "medium" | "large";
  petImage: string | null;
  /** Where the pet was left: its bottom right corner on the screen (DESKTOP §16 B18). */
  petCorner: { right: number; bottom: number } | null;
  /** Suggested follow-ups when a turn ends (Settings › Suggested prompts); off: each is one more turn on the model server. */
  suggestPrompts: boolean;
  /** The pull-request tab through the user's GitHub CLI (Settings › Git); off by default, refused at airgapped on. */
  pullRequests: boolean;
  /** When a finished turn shows a desktop notification, and whether a waiting approval or question does (Settings › Notifications). */
  notifyTurns: "unfocused" | "always" | "never";
  notifyApprovals: boolean;
  /** Questions from the agent and MCP servers have their own switch; null follows `notifyApprovals`. */
  notifyQuestions: boolean | null;
  /** Colour and font overrides of the §3.6 tokens (Settings › Appearance). */
  appearance: Appearance;
  /** Settings › Git: the prefix Create branch here offers, the instruction a commit message starts from, Force ticked by default. */
  branchPrefix: string;
  commitPrompt: string | null;
  forcePush: boolean;
  /** When the Scheduled view was last looked at (ISO time), for its unread count. */
  nightSeen: string | null;
  /** Where /review's findings go: into the thread, or a review thread of their own. */
  reviewDelivery: "inline" | "detached";
  /** The shell terminal tabs run, as a login shell; null for the user's `$SHELL`. */
  terminalShell: string | null;
  /** Commands in the thread: output open while running, always folded, or not shown. */
  commandOutput: "running" | "collapsed" | "hidden";
  /** Whether the bottom panel is open (Ctrl+J). */
  bottomPanel: boolean;
  /** The theme (Settings › Appearance): the system's, or light or dark whatever the system's (theme.ts). */
  theme: "system" | "light" | "dark";
}

/** A thread's worktree, where its work is now, and the snapshot kept when the worktree was removed. */
export interface WorktreeLink {
  checkout: string;
  /** The thread's folder inside the worktree. */
  cwd: string;
  /** The local checkout's root, and the folder the thread was started for. */
  source: string;
  sourceCwd: string;
  location: "worktree" | "local";
  snapshot: string | null;
}

type Spec = { default: unknown; enum?: string[]; type?: string; nullable?: boolean };
const SCHEMA = (schemaFile as { keys: Record<string, Spec> }).keys;

export const DEFAULT_PREFS: Prefs = Object.fromEntries(Object.entries(SCHEMA).map(([key, spec]) => [key, spec.default])) as unknown as Prefs;

/** Where a browser keeps them; theme.ts reads the theme from it before the first render. */
export const STORAGE_KEY = "mightling.prefs";

/** Keeps only the keys the schema has, with values of the right kind; the rest fall back. */
export function sanitize(stored: unknown): Prefs {
  const result: Record<string, unknown> = { ...DEFAULT_PREFS };
  if (typeof stored !== "object" || stored === null || Array.isArray(stored)) return result as unknown as Prefs;
  for (const [key, value] of Object.entries(stored)) {
    const spec = Object.prototype.hasOwnProperty.call(SCHEMA, key) ? SCHEMA[key] : undefined;
    if (!spec) continue;
    const ok = value === null
      ? Boolean(spec.nullable) || spec.default === null
      : spec.enum
        ? typeof value === "string" && spec.enum.includes(value)
        : spec.type === "strings"
          ? Array.isArray(value) && value.every((item) => typeof item === "string")
          : spec.type === "object"
            ? typeof value === "object" && !Array.isArray(value)
            : spec.type === "list"
              ? Array.isArray(value)
            : typeof value === spec.type;
    if (ok) result[key] = value;
  }
  return result as unknown as Prefs;
}

export async function loadPrefs(): Promise<Prefs> {
  if (bridge.host() === "electron") {
    try {
      return sanitize(await bridge.readPrefs());
    } catch {
      return { ...DEFAULT_PREFS };
    }
  }
  try {
    return sanitize(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null"));
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

/** Saves some preferences; resolves with all of them as stored. */
export async function savePrefs(current: Prefs, values: Partial<Prefs>): Promise<Prefs> {
  const next = { ...current, ...values };
  if (bridge.host() === "electron") return sanitize(await bridge.writePrefs(values as Record<string, unknown>));
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // A private window or blocked storage: the choice holds for this page only.
  }
  return next;
}

/** The colours Appearance may set for one theme. */
export interface AppearanceColours {
  accent?: string;
  canvas?: string;
  text?: string;
}

/**
 * The tokens Appearance may set: the accent, the canvas and the text over the light base, the same
 * three over the dark one (`dark`; its accent falls back to the light one's, as the themes share
 * Tiffany Blue), and the UI and code fonts for both.
 */
export interface Appearance extends AppearanceColours {
  fontUi?: string;
  fontCode?: string;
  dark?: AppearanceColours;
}

/** The tokens one theme's overrides set, by key of `appearanceFor`'s answer. */
export const APPEARANCE_TOKENS: Record<"accent" | "canvas" | "text" | "fontUi" | "fontCode", string> = {
  accent: "--accent",
  canvas: "--canvas",
  text: "--text",
  fontUi: "--font-ui",
  fontCode: "--font-code",
};

/** The readable font (DESKTOP §16 B23): bundled (fonts/, OFL 1.1) and made for legibility, Inter behind it for what it lacks. */
export const READABLE_FONT = '"Atkinson Hyperlegible", Inter, sans-serif';

const COLOUR = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

function cleanColours(entries: Record<string, unknown>): AppearanceColours {
  const result: AppearanceColours = {};
  for (const key of ["accent", "canvas", "text"] as const) {
    const colour = entries[key];
    if (typeof colour === "string" && COLOUR.test(colour)) result[key] = colour;
  }
  return result;
}

/** Only colours (#rgb, #rrggbb) and font lists of plain names reach the stylesheet. */
export function cleanAppearance(value: unknown): Appearance {
  if (typeof value !== "object" || value === null) return {};
  const entries = value as Record<string, unknown>;
  const result: Appearance = cleanColours(entries);
  for (const key of ["fontUi", "fontCode"] as const) {
    const font = entries[key];
    if (typeof font === "string" && font.length <= 200 && /^[\w\s"',.-]+$/.test(font)) result[key] = font;
  }
  if (typeof entries.dark === "object" && entries.dark !== null && !Array.isArray(entries.dark)) {
    const dark = cleanColours(entries.dark as Record<string, unknown>);
    if (Object.keys(dark).length > 0) result.dark = dark;
  }
  return result;
}

/** The overrides the shown theme takes: the light set's colours, or the dark set's (its accent falling back to the light one's); the fonts in both. */
export function appearanceFor(appearance: Appearance, theme: "light" | "dark"): Partial<Record<keyof typeof APPEARANCE_TOKENS, string>> {
  const clean = cleanAppearance(appearance);
  const colours: AppearanceColours = theme === "dark" ? { accent: clean.dark?.accent ?? clean.accent, canvas: clean.dark?.canvas, text: clean.dark?.text } : clean;
  return { accent: colours.accent, canvas: colours.canvas, text: colours.text, fontUi: clean.fontUi, fontCode: clean.fontCode };
}
