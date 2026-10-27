// The pet (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1, §2.15, Phase 3 item 6): a small
// companion in a window of its own, above the others, that shows what the agent is doing (resting,
// working, waiting for you, done) and opens Mightling or asks a quick question when clicked. Off
// until Settings › Pets turns it on (or Super+Alt+P). The art is Mightling's own mark, the faceted
// ling, or a picture the user picks; the vendor's pets are its trade dress (§2.21).

import fs from "node:fs";
import path from "node:path";
import { BrowserWindow, app, screen } from "electron";

import { WORK_URL } from "./app-protocol";
import { themeArgument, type Theme } from "./theme";

export type PetSize = "small" | "medium" | "large";

/** The pet's square, in the window's pixels, by size. */
export const PET_SIZES: Record<PetSize, number> = { small: 72, medium: 104, large: 144 };

/** The window around the pet while its bubble is open. */
export const BUBBLE = { width: 300, height: 290 };

export interface PetState {
  working: number;
  waiting: number;
  /** Turns finished since the pet was last clicked. */
  finished: number;
  size: PetSize;
  /** A picture of the user's own, as a data URL; null for Mightling's mark. */
  image: string | null;
  /** Threads that need the user (DESKTOP §16 B18): waiting for an answer here, or blocked. */
  needs: PetNeed[];
}

/** A thread the pet's bubble lists: waiting for an approval or a question, or blocked (it waits for one this window cannot answer, or the server reports an error). */
export interface PetNeed {
  id: string;
  title: string;
  why: "waiting" | "blocked";
}

/** The most threads the bubble lists. */
export const NEEDS_SHOWN = 5;

/** What the agent is up to, followed from the app-server's messages as the main process routes them. */
export class PetStatus {
  private readonly running = new Set<string>();
  /** Open requests for the user (approvals, questions), by request id, with their thread. */
  private readonly asking = new Map<string, string | null>();
  private finished = 0;
  /** Each thread's name or first words, learnt from the messages that carry threads. */
  private readonly names = new Map<string, string>();
  /** Threads the server reports as waiting on the user, or in error (`thread/status/changed`). */
  private readonly flagged = new Map<string, "waiting" | "error">();

  private learn(thread: unknown): void {
    if (typeof thread !== "object" || thread === null) return;
    const { id, name, preview } = thread as { id?: unknown; name?: unknown; preview?: unknown };
    if (typeof id !== "string") return;
    const label = typeof name === "string" && name.trim() ? name.trim() : typeof preview === "string" && preview.trim() ? preview.trim() : "";
    if (label) this.names.set(id, label.replace(/\s+/g, " ").slice(0, 80));
  }

  /** Takes one message from the server; true when the pet's state changed. */
  observe(message: unknown): boolean {
    if (typeof message !== "object" || message === null) return false;
    const { id, method, params, result } = message as { id?: unknown; method?: unknown; params?: Record<string, unknown>; result?: Record<string, unknown> };
    if (result && typeof result === "object") {
      this.learn(result.thread);
      if (Array.isArray(result.data)) for (const thread of result.data) this.learn(thread);
    }
    if (typeof method !== "string") return false;
    const thread = typeof params?.threadId === "string" ? params.threadId : null;
    if (method === "thread/started") {
      this.learn(params?.thread);
      return false;
    }
    if (method === "thread/name/updated" && thread && typeof params?.threadName === "string") {
      this.names.set(thread, params.threadName.slice(0, 80));
      return false;
    }
    if (method === "thread/status/changed" && thread) {
      const status = (params?.status ?? {}) as { type?: unknown; activeFlags?: unknown };
      const flags = Array.isArray(status.activeFlags) ? status.activeFlags : [];
      const next = status.type === "systemError" ? "error" : status.type === "active" && flags.length > 0 ? "waiting" : null;
      const before = this.flagged.get(thread) ?? null;
      if (next) this.flagged.set(thread, next);
      else this.flagged.delete(thread);
      return before !== next;
    }
    if (method === "turn/started" && thread) {
      this.running.add(thread);
      return true;
    }
    if (method === "turn/completed" && thread) {
      this.running.delete(thread);
      for (const [request, owner] of this.asking) if (owner === thread) this.asking.delete(request);
      this.finished++;
      return true;
    }
    if (id !== undefined && (method.endsWith("/requestApproval") || method === "item/tool/requestUserInput" || method === "mcpServer/elicitation/request" || method === "execCommandApproval" || method === "applyPatchApproval")) {
      this.asking.set(String(id), thread);
      return true;
    }
    if (method === "serverRequest/resolved" && params?.requestId !== undefined) {
      return this.asking.delete(String(params.requestId));
    }
    return false;
  }

  /** The user answered a request through the window (the page's reply, not a server message). */
  answered(requestId: unknown): boolean {
    return this.asking.delete(String(requestId));
  }

  seen(): void {
    this.finished = 0;
  }

  reset(): void {
    this.running.clear();
    this.asking.clear();
    this.flagged.clear();
  }

  /** The threads that need the user: waiting for an answer this window holds, then blocked ones. */
  needs(): PetNeed[] {
    const waiting = new Set([...this.asking.values()].filter((owner): owner is string => Boolean(owner)));
    const title = (id: string) => this.names.get(id) ?? "A thread";
    const list: PetNeed[] = [...waiting].map((id) => ({ id, title: title(id), why: "waiting" as const }));
    for (const id of this.flagged.keys()) {
      if (waiting.has(id)) continue;
      // Waiting on an answer no open request here can give, or in error: the thread cannot go on by itself.
      list.push({ id, title: title(id), why: "blocked" });
    }
    return list.slice(0, NEEDS_SHOWN);
  }

  /** Whether `id` is one of the threads the bubble lists, so a click may open it. */
  listed(id: unknown): id is string {
    return typeof id === "string" && this.needs().some((need) => need.id === id);
  }

  counts(): { working: number; waiting: number; finished: number } {
    return { working: this.running.size, waiting: this.asking.size, finished: this.finished };
  }

  /** The thread a click should open: one waiting for the user first, else one working. */
  focus(): string | null {
    for (const owner of this.asking.values()) if (owner) return owner;
    return this.running.values().next().value ?? null;
  }
}

/** A picture the user picked for the pet, as a data URL (PNG, GIF, WebP, SVG, JPEG; 2 MB at most), else null. */
export function petImage(file: unknown): string | null {
  if (typeof file !== "string" || !path.isAbsolute(file)) return null;
  const types: Record<string, string> = { ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg" };
  const type = types[path.extname(file).toLowerCase()];
  try {
    if (!type || fs.statSync(file).size > 2 * 1024 * 1024) return null;
    return `data:${type};base64,${fs.readFileSync(file).toString("base64")}`;
  } catch {
    return null;
  }
}

/** Where the pet's bottom right corner is kept (`petCorner`), the corner its bubble grows from. */
export interface PetCorner {
  right: number;
  bottom: number;
}

/**
 * Where the pet opens: its kept corner when that lies on one of the displays now attached (moved inside
 * that display's work area), else the bottom right of the primary display. Pure, for its tests.
 */
export function petBounds(side: number, corner: unknown, areas: { x: number; y: number; width: number; height: number }[], primary: { x: number; y: number; width: number; height: number }): { x: number; y: number } {
  const kept = corner as Partial<PetCorner> | null;
  if (kept && Number.isFinite(kept.right) && Number.isFinite(kept.bottom)) {
    const right = kept.right as number;
    const bottom = kept.bottom as number;
    const area = areas.find((entry) => right > entry.x && right <= entry.x + entry.width + side && bottom > entry.y && bottom <= entry.y + entry.height + side);
    if (area) {
      const x = Math.min(Math.max(right - side, area.x), area.x + area.width - side);
      const y = Math.min(Math.max(bottom - side, area.y), area.y + area.height - side);
      return { x: Math.round(x), y: Math.round(y) };
    }
  }
  return { x: primary.x + primary.width - side - 24, y: primary.y + primary.height - side - 24 };
}

/** The pet's window: frameless, transparent, above the others, not in the task bar; where it was left, else bottom right. */
export function openPet(preload: string, size: PetSize, show: boolean, theme: Theme, corner: unknown = null): BrowserWindow {
  const side = PET_SIZES[size];
  const at = petBounds(side, corner, screen.getAllDisplays().map((display) => display.workArea), screen.getPrimaryDisplay().workArea);
  const window = new BrowserWindow({
    width: side,
    height: side,
    x: at.x,
    y: at.y,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    show: false,
    title: "Mightling pet",
    backgroundColor: "#00000000",
    webPreferences: {
      preload,
      // The bubble's colours: the theme's from the first paint (theme.ts).
      additionalArguments: [themeArgument(theme)],
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
      devTools: !app.isPackaged,
    },
  });
  window.setAlwaysOnTop(true, "floating");
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  if (show) window.once("ready-to-show", () => window.showInactive());
  void window.loadURL(`${WORK_URL}#pet`);
  return window;
}

/** Grows the pet's window for its bubble (anchored at the bottom right, where the pet sits), or back. */
export function fitPet(window: BrowserWindow, size: PetSize, open: boolean): void {
  const side = PET_SIZES[size];
  const bounds = window.getBounds();
  const width = open ? Math.max(BUBBLE.width, side) : side;
  const height = open ? side + BUBBLE.height : side;
  window.setBounds({ x: bounds.x + bounds.width - width, y: bounds.y + bounds.height - height, width, height });
}
