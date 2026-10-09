// The app window (the former Work window): the Mightling UI from `app://-/index.html`, Ask and Work
// one click apart, frameless with native controls, as the Codex app's primary window is on Linux
// (`titleBarStyle: hidden`, a transparent `titleBarOverlay`, the page drawing its own title bar),
// its bounds remembered between runs.

import path from "node:path";
import { BrowserWindow, app, nativeTheme, shell } from "electron";

import appConfig from "../app.json";
import { WORK_URL } from "./app-protocol";
import { track } from "./window-state";

export interface WorkOptions {
  preload: string;
  show: boolean;
  /** The view the page opens on: Ask, or Work (`#work`), as `ling web`'s page does. */
  view?: "ask" | "work";
}

export function openWork(options: WorkOptions): BrowserWindow {
  const { work } = appConfig;
  const dark = nativeTheme.shouldUseDarkColors;
  const window = new BrowserWindow({
    title: work.title,
    width: work.width,
    height: work.height,
    minWidth: work.minWidth,
    minHeight: work.minHeight,
    show: options.show,
    center: true,
    backgroundColor: "#00000000",
    autoHideMenuBar: true,
    titleBarStyle: "hidden",
    titleBarOverlay: { color: "#00000000", symbolColor: dark ? "#ffffff" : "#1f1f1f", height: 36 },
    webPreferences: {
      preload: options.preload,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: true,
      devTools: !app.isPackaged,
    },
  });
  track(window, path.join(app.getPath("userData"), "window-state-work.json"), { width: work.minWidth, height: work.minHeight });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  // The page is bundled; it never navigates anywhere else.
  window.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(WORK_URL)) event.preventDefault();
  });
  void window.loadURL(options.view === "ask" ? WORK_URL : `${WORK_URL}#work`);
  return window;
}
