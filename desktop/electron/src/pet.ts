// The pet (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1, §2.15, Phase 3 item 6): a small
// companion in a window of its own, above the others, that shows what the agent is doing (resting,
// working, waiting for you, done) and opens Mightling or asks a quick question when clicked. Off
// until Settings › Pets turns it on (or Super+Alt+P). The art is Mightling's own mark, the faceted
// ling, or a picture the user picks; the vendor's pets are its trade dress (§2.21).

import fs from "node:fs";
import path from "node:path";
import { BrowserWindow, app, screen } from "electron";

import { WORK_URL } from "./app-protocol";

export type PetSize = "small" | "medium" | "large";

/** The pet's square, in the window's pixels, by size. */
export const PET_SIZES: Record<PetSize, number> = { small: 72, medium: 104, large: 144 };

/** The window around the pet while its bubble is open. */
export const BUBBLE = { width: 300, height: 190 };

export interface PetState {
  working: number;
  waiting: number;
  /** Turns finished since the pet was last clicked. */
  finished: number;
  size: PetSize;
  /** A picture of the user's own, as a data URL; null for Mightling's mark. */
  image: string | null;
}

/** What the agent is up to, followed from the app-server's messages as the main process routes them. */
export class PetStatus {
  private readonly running = new Set<string>();
  /** Open requests for the user (approvals, questions), by request id, with their thread. */
  private readonly asking = new Map<string, string | null>();
  private finished = 0;

  /** Takes one message from the server; true when the pet's state changed. */
  observe(message: unknown): boolean {
    if (typeof message !== "object" || message === null) return false;
    const { id, method, params } = message as { id?: unknown; method?: unknown; params?: Record<string, unknown> };
    if (typeof method !== "string") return false;
    const thread = typeof params?.threadId === "string" ? params.threadId : null;
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
    if (id !== undefined && (method.endsWith("/requestApproval") || method === "item/tool/requestUserInput" || method === "execCommandApproval" || method === "applyPatchApproval")) {
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

/** The pet's window: frameless, transparent, above the others, not in the task bar; bottom right to begin with. */
export function openPet(preload: string, size: PetSize, show: boolean): BrowserWindow {
  const side = PET_SIZES[size];
  const area = screen.getPrimaryDisplay().workArea;
  const window = new BrowserWindow({
    width: side,
    height: side,
    x: area.x + area.width - side - 24,
    y: area.y + area.height - side - 24,
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
