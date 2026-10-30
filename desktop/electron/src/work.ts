// The app's windows (DESKTOP_ELECTRON §13): every one shows the Mightling UI from
// `app://-/index.html`, frameless with native controls, as the Codex app's primary window is on Linux
// (`titleBarStyle: hidden`, a transparent `titleBarOverlay`, the page drawing its own title bar). A
// full window shows Ask and Work one click apart; a pop-out one thread (`#popout=<id>`); a detached
// pane one thread's terminal or diff (`#pane=<kind>&thread=<id>`). All are made here, with the same
// preload, the same renderer sandbox, the same navigation lock and the same CSP (the `app://`
// scheme sends it): no window of the app loads anything but the bundled page. Its background and the
// overlay's symbols are the theme's (theme.ts), and the preload reads the theme from its arguments
// before the first paint.

import path from "node:path";
import { BrowserWindow, app, type Rectangle } from "electron";

import appConfig from "../app.json";
import { WORK_URL } from "./app-protocol";
import { themeArgument, windowColours, type Theme } from "./theme";
import { place, track } from "./window-state";
import type { PaneKind } from "./windows";

export interface WorkOptions {
  preload: string;
  show: boolean;
  /** The view the page opens on: Ask, or Work (`#work`), as `ling web`'s page does. */
  view?: "ask" | "work";
  /** A pop-out: one thread in a window of its own (`#popout=<id>`, DESKTOP_PARITY §2.1). */
  popout?: string;
  /** A detached pane: one thread's terminal or diff in a window of its own. */
  pane?: { kind: PaneKind; thread: string };
  /** Where a full window opens (the session's, or beside the window it came from); else the default size, centred. */
  bounds?: Rectangle;
  maximized?: boolean;
  /** The theme the window opens in (main.ts, from `nativeTheme`). */
  theme: Theme;
}

/** The page's address for a window: only ever the bundled page, with what it shows in the fragment. */
export function pageUrl(options: Pick<WorkOptions, "view" | "popout" | "pane">): string {
  if (options.pane) return `${WORK_URL}#pane=${options.pane.kind}&thread=${encodeURIComponent(options.pane.thread)}`;
  if (options.popout) return `${WORK_URL}#popout=${encodeURIComponent(options.popout)}`;
  return options.view === "ask" ? WORK_URL : `${WORK_URL}#work`;
}

/** Whether a window of the app may go to `url`: the bundled page alone. */
export function staysOnPage(url: string): boolean {
  return url === WORK_URL || url.startsWith(`${WORK_URL}#`);
}

export function openWork(options: WorkOptions): BrowserWindow {
  const { work } = appConfig;
  const size = options.pane ? (options.pane.kind === "terminal" ? { width: 900, height: 460 } : { width: 980, height: 820 }) : options.popout ? { width: 720, height: 820 } : { width: work.width, height: work.height };
  const window = new BrowserWindow({
    title: work.title,
    ...size,
    minWidth: work.minWidth,
    minHeight: options.pane ? 240 : work.minHeight,
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
      webviewTag: false,
      spellcheck: true,
      devTools: !app.isPackaged,
    },
  });
  const min = { width: work.minWidth, height: options.pane ? 240 : work.minHeight };
  if (options.pane) track(window, path.join(app.getPath("userData"), `window-state-pane-${options.pane.kind}.json`), min);
  else if (options.popout) track(window, path.join(app.getPath("userData"), "window-state-popout.json"), min);
  else if (options.bounds) place(window, { bounds: options.bounds, maximized: options.maximized }, min);
  lockDown(window);
  void window.loadURL(pageUrl(options));
  return window;
}

/** The navigation lock every window of the app gets: no other page, no new window, no embedded guest. */
function lockDown(window: BrowserWindow): void {
  const contents = window.webContents;
  // Never a window, never an external open from here: the page's links go through its own link/open
  // message (links.ts rules, and the air-gap check `offline()` that window.open would skip).
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  // The page is bundled; it never navigates anywhere else, nor is redirected there.
  contents.on("will-navigate", (event, url) => {
    if (!staysOnPage(url)) event.preventDefault();
  });
  contents.on("will-redirect", (event, url) => {
    if (!staysOnPage(url)) event.preventDefault();
  });
  contents.on("will-attach-webview", (event) => event.preventDefault());
}
