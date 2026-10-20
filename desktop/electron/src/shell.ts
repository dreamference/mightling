// The desktop furniture the Codex app has and Tauri's shell did not: an application menu, a
// context menu the page asks for over IPC, a tray icon, a power-save blocker while a turn runs,
// and a notification when a turn ends in a window that is not focused.

import path from "node:path";
import { BrowserWindow, Menu, Notification, Tray, app, nativeImage, powerSaveBlocker } from "electron";

export interface ShellActions {
  /** The app window on Ask (the menu's former Chat, a window of its own until 2026-10-09). */
  openAsk(): void;
  /** The app window on Work. */
  openWork(): void;
}

export function installMenu(actions: ShellActions): void {
  // The page handles every shortcut itself, from its keymap (desktop/ui/src/keymap.json, rebindable
  // in Settings), so the menu shows the keys without registering them (`registerAccelerator: false`):
  // Ctrl+W closes a tab in the page, never the window; Alt+1/Alt+2 switch the view (DESKTOP_PARITY
  // §2.19), leaving Ctrl+1–9 for threads.
  const shown = (accelerator: string) => ({ accelerator, registerAccelerator: false });
  const template: Electron.MenuItemConstructorOptions[] = [
    // On a Mac the first menu is the application's (About, Hide, Quit), named after it.
    ...(process.platform === "darwin" ? [{ role: "appMenu" } as const] : []),
    {
      label: "File",
      submenu: [
        { label: "Ask", ...shown("Alt+1"), click: () => actions.openAsk() },
        { label: "Work", ...shown("Alt+2"), click: () => actions.openWork() },
        { type: "separator" },
        { label: "Quit", ...shown("CmdOrCtrl+Q"), click: () => app.quit() },
      ],
    },
    { label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [{ label: "Full screen", ...shown("F11"), click: (_item, window) => window?.setFullScreen(!window.isFullScreen()) }, ...(app.isPackaged ? [] : [{ role: "toggleDevTools" } as const])] },
    { label: "Window", submenu: [{ role: "minimize" }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

/** The context menu the page asks for: edit items on an editable field, copy on a selection. */
export function showContextMenu(window: BrowserWindow, request: { x: number; y: number; editable: boolean; selection: string }): void {
  const items: Electron.MenuItemConstructorOptions[] = request.editable
    ? [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }]
    : request.selection
      ? [{ role: "copy" }, { role: "selectAll" }]
      : [{ role: "selectAll" }];
  Menu.buildFromTemplate(items).popup({ window, x: request.x, y: request.y });
}

let tray: Tray | null = null;

export function installTray(iconsDir: string, actions: ShellActions): void {
  let icon = nativeImage.createFromPath(path.join(iconsDir, "32x32.png"));
  if (icon.isEmpty()) return;
  // The macOS menu bar is 22 points tall; the 32-pixel image would be drawn 32 points high there.
  if (process.platform === "darwin") icon = icon.resize({ height: 18 });
  tray = new Tray(icon);
  tray.setToolTip("Mightling");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Ask", click: () => actions.openAsk() },
      { label: "Work", click: () => actions.openWork() },
      { type: "separator" },
      { role: "quit" },
    ]),
  );
}

let blocker: number | null = null;

/** Keeps the machine awake while a turn runs; released when none does. */
export function keepAwake(busy: boolean): void {
  if (busy && blocker === null) blocker = powerSaveBlocker.start("prevent-app-suspension");
  else if (!busy && blocker !== null) {
    powerSaveBlocker.stop(blocker);
    blocker = null;
  }
}

/** A desktop notification, silent, when the window is not in front (or always when asked); a click runs `onClick`. */
/**
 * Shows a notification unless the app is in front: "in front" is any of its windows focused, a
 * pop-out as much as the main window (Settings › Notifications' "when the window is not in front").
 */
export function notify(windows: BrowserWindow[], notice: { title: string; body: string; always: boolean }, onClick: () => void): void {
  const inFront = windows.some((each) => !each.isDestroyed() && each.isFocused());
  if ((!notice.always && inFront) || !Notification.isSupported()) return;
  const notification = new Notification({ title: notice.title, body: notice.body, silent: true });
  notification.on("click", onClick);
  notification.show();
}
