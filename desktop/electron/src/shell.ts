// The desktop furniture the Codex app has and Tauri's shell did not: an application menu, a
// context menu the page asks for over IPC, a tray icon, a power-save blocker while a turn runs,
// and a notification when a turn ends in a window that is not focused.

import path from "node:path";
import { BrowserWindow, Menu, Notification, Tray, app, nativeImage, powerSaveBlocker } from "electron";

export interface ShellActions {
  /** The most recent window on Ask (the menu's former Chat, a window of its own until 2026-10-09), or a new one when none is open. */
  openAsk(): void;
  /** The most recent window on Work, or a new one. */
  openWork(): void;
  /** New Window (DESKTOP_ELECTRON §13): another full window, beside the one in front. */
  newWindow?(from: BrowserWindow | null): void;
  /** The Window menu's Terminal and Diff items: the window in front detaches its pane, or docks it back. */
  pane?(kind: "terminal" | "review", from: BrowserWindow | null): void;
  /** Quick Entry's box (quick.ts). */
  quickQuestion?(): void;
  /** File's New thread, Open folder… and Settings… in the window in front (DESKTOP §16 B20). */
  command?(command: "newChat" | "openFolder" | "settings", from: BrowserWindow | null): void;
  /** Help › Show logs: the folder the app's and its agent's logs are in. */
  showLogs?(): void;
}

export function installMenu(actions: ShellActions): void {
  // The page handles every shortcut itself, from its keymap (desktop/ui/src/keymap.json, rebindable
  // in Settings), so the menu shows the keys without registering them (`registerAccelerator: false`):
  // Ctrl+W closes a tab in the page, never the window; Alt+1/Alt+2 switch the view (DESKTOP_PARITY
  // §2.19), leaving Ctrl+1–9 for threads; Ctrl+Shift+N (Cmd+Shift+N on a Mac) opens a new window.
  const shown = (accelerator: string) => ({ accelerator, registerAccelerator: false });
  const template: Electron.MenuItemConstructorOptions[] = [
    // On a Mac the first menu is the application's (About, Hide, Quit), named after it.
    ...(process.platform === "darwin" ? [{ role: "appMenu" } as const] : []),
    {
      label: "File",
      submenu: [
        ...(actions.newWindow ? [{ label: "New Window", ...shown("CmdOrCtrl+Shift+N"), click: (_item: Electron.MenuItem, window: Electron.BaseWindow | undefined) => actions.newWindow?.(window instanceof BrowserWindow ? window : null) }, { type: "separator" } as const] : []),
        ...(actions.command ? [
          { label: "New Thread", ...shown("CmdOrCtrl+N"), click: (_item: Electron.MenuItem, window: Electron.BaseWindow | undefined) => actions.command?.("newChat", window instanceof BrowserWindow ? window : null) },
          { label: "Open Folder…", ...shown("CmdOrCtrl+O"), click: (_item: Electron.MenuItem, window: Electron.BaseWindow | undefined) => actions.command?.("openFolder", window instanceof BrowserWindow ? window : null) },
          { type: "separator" } as const,
        ] : []),
        { label: "Ask", ...shown("Alt+1"), click: () => actions.openAsk() },
        { label: "Work", ...shown("Alt+2"), click: () => actions.openWork() },
        ...(actions.command ? [
          { type: "separator" } as const,
          { label: "Settings…", ...shown("CmdOrCtrl+,"), click: (_item: Electron.MenuItem, window: Electron.BaseWindow | undefined) => actions.command?.("settings", window instanceof BrowserWindow ? window : null) },
        ] : []),
        { type: "separator" },
        { label: "Quit", ...shown("CmdOrCtrl+Q"), click: () => app.quit() },
      ],
    },
    { label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [{ label: "Full screen", ...shown("F11"), click: (_item, window) => window?.setFullScreen(!window.isFullScreen()) }, ...(app.isPackaged ? [] : [{ role: "toggleDevTools" } as const])] },
    {
      label: "Window",
      submenu: [
        { role: "minimize" },
        ...(actions.pane ? [
          { type: "separator" } as const,
          // In the window it came from: into a window of its own; in the pane's own window, or once detached: back.
          { label: "Terminal in Its Own Window", click: (_item: Electron.MenuItem, window: Electron.BaseWindow | undefined) => actions.pane?.("terminal", window instanceof BrowserWindow ? window : null) },
          { label: "Diff in Its Own Window", click: (_item: Electron.MenuItem, window: Electron.BaseWindow | undefined) => actions.pane?.("review", window instanceof BrowserWindow ? window : null) },
        ] : []),
      ],
    },
    ...(actions.showLogs ? [{ label: "Help", submenu: [{ label: "Show Logs", click: () => actions.showLogs?.() }] }] : []),
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

/**
 * The tray's image in icons/. Windows draws it 16 pixels high at 100 % scaling and up to 48 at 300 %:
 * `tray.png` is the 16-pixel one, and `nativeImage` loads its `@1.25x` … `@3x` siblings beside it, so
 * each display gets one drawn at its size, never the 32-pixel image squeezed. Elsewhere, as before.
 */
export function trayIconFile(platform: NodeJS.Platform): string {
  return platform === "win32" ? "tray.png" : "32x32.png";
}

export function installTray(iconsDir: string, actions: ShellActions): void {
  let icon = nativeImage.createFromPath(path.join(iconsDir, trayIconFile(process.platform)));
  if (icon.isEmpty()) return;
  // The macOS menu bar is 22 points tall; the 32-pixel image would be drawn 32 points high there.
  if (process.platform === "darwin") icon = icon.resize({ height: 18 });
  tray = new Tray(icon);
  trayActions = actions;
  updateTray({ working: 0, waiting: 0 });
}

let trayActions: ShellActions | null = null;

/**
 * What the tray says about the threads: how many work and how many wait for the user (DESKTOP §16 B20),
 * and how many of Night Shift's results are unread (B41). Pure.
 */
export function trayStatus(counts: { working: number; waiting: number; night?: number }): string | null {
  const parts = [
    counts.waiting ? `${counts.waiting} waiting for you` : "",
    counts.working ? `${counts.working} working` : "",
    counts.night ? `${counts.night} night ${counts.night === 1 ? "result" : "results"} unread` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

/** The tray's tooltip and menu, with the threads' counts at their top. */
export function updateTray(counts: { working: number; waiting: number; night?: number }): void {
  const actions = trayActions;
  if (!tray || tray.isDestroyed() || !actions) return;
  const status = trayStatus(counts);
  tray.setToolTip(status ? `Mightling: ${status}` : "Mightling");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      ...(status ? [{ label: status, enabled: false }, { type: "separator" } as const] : []),
      { label: "Ask", click: () => actions.openAsk() },
      { label: "Work", click: () => actions.openWork() },
      ...(actions.quickQuestion ? [{ label: "New question…", click: () => actions.quickQuestion?.() }] : []),
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
