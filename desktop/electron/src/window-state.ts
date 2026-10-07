// Window bounds persisted between runs, as the Codex app persists them: saved on move and resize,
// restored clamped to a display's work area, with the maximised state.

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

/** Restores `window` from `file` and keeps the file up to date while it lives. */
export function track(window: BrowserWindow, file: string, min: { width: number; height: number }): void {
  const state = read(file);
  const area = state.bounds ? screen.getDisplayMatching(state.bounds).workArea : screen.getPrimaryDisplay().workArea;
  const bounds = clamped(state.bounds, area, min);
  if (bounds) window.setBounds(bounds);
  if (state.maximized) window.maximize();
  let timer: NodeJS.Timeout | null = null;
  const save = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      const current: WindowState = { bounds: window.isMaximized() ? state.bounds : window.getNormalBounds(), maximized: window.isMaximized() };
      state.bounds = current.bounds;
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(file, JSON.stringify(current));
      } catch {
        // Not worth a dialog.
      }
    }, 300);
  };
  window.on("resize", save);
  window.on("move", save);
  window.on("maximize", save);
  window.on("unmaximize", save);
}
