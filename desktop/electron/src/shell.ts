// The desktop furniture the Codex app has and Tauri's shell did not: an application menu, a
// context menu the page asks for over IPC, a tray icon, a power-save blocker while a turn runs,
// and a notification when a turn ends in a window that is not focused.

import path from "node:path";
import { BrowserWindow, Menu, Notification, Tray, app, nativeImage, powerSaveBlocker } from "electron";

export interface ShellActions {
  openChat(): void;
  openWork(): void;
}

export function installMenu(actions: ShellActions): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: "File",
      submenu: [
        { label: "Chat", accelerator: "CmdOrCtrl+1", click: () => actions.openChat() },
        { label: "Work", accelerator: "CmdOrCtrl+2", click: () => actions.openWork() },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [{ role: "reload" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }, ...(app.isPackaged ? [] : [{ role: "toggleDevTools" } as const])] },
    { label: "Window", submenu: [{ role: "minimize" }, { role: "close" }] },
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
  const icon = nativeImage.createFromPath(path.join(iconsDir, "32x32.png"));
  if (icon.isEmpty()) return;
  tray = new Tray(icon);
  tray.setToolTip("Mightling");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Chat", click: () => actions.openChat() },
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

/** Tells the user a turn finished when Work's window is not the one in front. */
export function notifyTurnDone(window: BrowserWindow | null, body: string): void {
  if (window?.isFocused() || !Notification.isSupported()) return;
  const notification = new Notification({ title: "Mightling", body, silent: true });
  notification.on("click", () => window?.show());
  notification.show();
}
