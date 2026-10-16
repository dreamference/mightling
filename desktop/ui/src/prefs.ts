// The window's own preferences, as the page sees them (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md
// §2.15): in the desktop app the main process keeps them (desktop/electron/src/prefs.ts) and checks
// every write against prefs.json; in a browser they live in localStorage, which may be missing or
// refuse, so every read falls back to the defaults.

import * as bridge from "./bridge";
import schemaFile from "./prefs.json";

export interface Prefs {
  /** What Enter does in the composer: send (Shift+Enter for a new line), or a new line (Ctrl+Enter sends). */
  enterBehaviour: "enter" | "ctrlEnter";
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
  /** Worktrees made permanent: projects of their own, never cleaned up. */
  permanentWorktrees: string[];
  /** Where /review's findings go: into the thread, or a review thread of their own. */
  reviewDelivery: "inline" | "detached";
  /** Whether the bottom panel is open (Ctrl+J). */
  bottomPanel: boolean;
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

const STORAGE_KEY = "mightling.prefs";

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
