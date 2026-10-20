// The app window (the former Work window): the Mightling UI from `app://-/index.html`, Ask and Work
// one click apart, frameless with native controls, as the Codex app's primary window is on Linux
// (`titleBarStyle: hidden`, a transparent `titleBarOverlay`, the page drawing its own title bar),
// its bounds remembered between runs. Its background and the overlay's symbols are the theme's
// (theme.ts), and the preload reads the theme from its arguments before the first paint.

import path from "node:path";
import { BrowserWindow, app, shell } from "electron";

import appConfig from "../app.json";
import { WORK_URL } from "./app-protocol";
import { themeArgument, windowColours, type Theme } from "./theme";
import { track } from "./window-state";

export interface WorkOptions {
  preload: string;
  show: boolean;
  /** The view the page opens on: Ask, or Work (`#work`), as `ling web`'s page does. */
  view?: "ask" | "work";
  /** A pop-out: one thread in a window of its own (`#popout=<id>`, DESKTOP_PARITY §2.1). */
  popout?: string;
  /** The theme the window opens in (main.ts, from `nativeTheme`). */
  theme: Theme;
}

export function openWork(options: WorkOptions): BrowserWindow {
  const { work } = appConfig;
  const window = new BrowserWindow({
    title: work.title,
    width: options.popout ? 720 : work.width,
    height: options.popout ? 820 : work.height,
    minWidth: work.minWidth,
    minHeight: work.minHeight,
    show: options.show,
    center: true,
    ...windowColours(options.theme),
    autoHideMenuBar: true,
    titleBarStyle: "hidden",
    webPreferences: {
      preload: options.preload,
      additionalArguments: [themeArgument(options.theme)],
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: true,
      devTools: !app.isPackaged,
    },
  });
  track(window, path.join(app.getPath("userData"), options.popout ? "window-state-popout.json" : "window-state-work.json"), { width: work.minWidth, height: work.minHeight });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  // The page is bundled; it never navigates anywhere else.
  window.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(WORK_URL)) event.preventDefault();
  });
  void window.loadURL(options.popout ? `${WORK_URL}#popout=${encodeURIComponent(options.popout)}` : options.view === "ask" ? WORK_URL : `${WORK_URL}#work`);
  return window;
}
