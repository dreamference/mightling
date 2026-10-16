// The app window and the one IPC channel between its page and this process
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3). Every message from the page is checked to
// come from that window before it is acted on. The app window (`app://`) shows Ask and Work, as
// `ling web` does: this process enforces the same policy (policy.ts) and keeps the Ask folders
// (ask.ts). It is the app's only window, and its app-server the app's only one: the menu's Ask,
// the tray's and `ling app` alone show Ask in it. Until 2026-10-09 they opened a second window on
// `ling web`, whose own app-server shared `~/.mightling` with this one
// (specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6).

import path from "node:path";
import { BrowserWindow, app, ipcMain, shell, type IpcMainInvokeEvent } from "electron";

import { CHANNEL_CHUNK_ACK, CHANNEL_FOR_VIEW, CHANNEL_FROM_VIEW, Reassembler, chunked, isChunk, type ForView, type FromView } from "./api";
import { airgapped, setDefault } from "./airgapped";
import { serve } from "./app-protocol";
import type { WorkTarget } from "./args";
import { findLing } from "./bridge";
import { findEditors, openCommand, type Editor } from "./editors";
import * as git from "./git";
import { Prefs } from "./prefs";
import { AppServer } from "./server";
import { installMenu, installTray, keepAwake, notifyTurnDone, showContextMenu } from "./shell";
import { openWork } from "./work";

export interface MainOptions {
  /** What `ling app` asked for: Work with a folder or thread, or `null` for Ask. */
  work: WorkTarget | null;
  /** The audit's session: the window hidden, its app-server started, quit after this many seconds. */
  audit: number | null;
}

type View = "ask" | "work";

const resourcesPath = () => (app.isPackaged ? process.resourcesPath : null);

export async function main(options: MainOptions): Promise<void> {
  // The page is the built renderer beside this bundle, packaged or not: app:// serves files,
  // never Vite's dev server.
  serve(path.join(__dirname, "..", "renderer", "main_window"));

  let window: BrowserWindow | null = null;
  let target: WorkTarget = options.work ?? { cwd: null, thread: null };
  // Hidden for the audit's session and for an end-to-end test (`MIGHTLING_HIDDEN=1`).
  const show = options.audit === null && !process.env.MIGHTLING_HIDDEN;

  const deliver = (message: ForView) => {
    const contents = window?.webContents;
    if (!contents || contents.isDestroyed()) return;
    for (const piece of chunked(message)) contents.send(CHANNEL_FOR_VIEW, piece);
  };
  const server = new AppServer(resourcesPath(), {
    deliver,
    busyChanged: (busy) => {
      keepAwake(busy);
      if (!busy) notifyTurnDone(window, "The agent finished a turn.");
    },
  });

  /** Brings the app window forward on `view`, opening it when it is not open. */
  const showView = (view: View) => {
    if (window && !window.isDestroyed()) {
      if (window.isMinimized()) window.restore();
      if (show) {
        window.show();
        window.focus();
      }
      deliver({ channel: "view", payload: view });
      return;
    }
    window = openWork({ preload: path.join(__dirname, "preload.cjs"), show, view });
    window.on("closed", () => (window = null));
  };
  const prefs = new Prefs(app.getPath("userData"));
  const actions = { openAsk: () => showView("ask"), openWork: () => showView("work") };

  installMenu(actions);
  // `.vite/build/../../icons`: inside the asar when packaged, the project's folder otherwise.
  installTray(path.join(__dirname, "..", "..", "icons"), actions);

  // The one channel from the page.
  const fromView = new Reassembler();
  ipcMain.handle(CHANNEL_FROM_VIEW, async (event: IpcMainInvokeEvent, data: unknown) => {
    if (!window || event.sender.id !== window.webContents.id) throw new Error("only the app window talks to the agent");
    let message = data;
    if (isChunk(data)) {
      message = fromView.take(data);
      if (message === undefined) return null;
    }
    return handle(message as FromView);
  });
  ipcMain.on(CHANNEL_CHUNK_ACK, () => {});

  /** Refuses what would reach the network for a thread at `/airgapped on` (push, the forge's page). */
  const offline = async (thread: string | null, what: string) => {
    const ling = findLing(resourcesPath());
    const level = ling ? await airgapped(ling, thread) : { level: "off", source: "" };
    if (level.level === "on") throw new Error(`airgapped is on (${level.source}): ${what} would reach the network`);
  };
  let editors: Editor[] | null = null;

  async function handle(message: FromView): Promise<unknown> {
    switch (message.type) {
      case "work/start":
        return server.start();
      case "work/send":
        await server.send(message.message);
        return null;
      case "ask/upload":
        return server.upload(message);
      case "work/stop":
        server.stop();
        return null;
      case "work/target":
        return target;
      case "work/airgapped": {
        const ling = findLing(resourcesPath());
        return ling ? airgapped(ling, message.thread) : { level: "off", source: "ling is not installed" };
      }
      case "work/airgapped-default": {
        const ling = findLing(resourcesPath());
        if (!ling) throw new Error("ling is not installed");
        return setDefault(ling, message.level);
      }
      case "context-menu":
        if (window) showContextMenu(window, message);
        return null;
      case "git/repos":
        return git.repositories(message.cwd);
      case "git/status":
        return git.status(await git.checkRoot(message.root));
      case "git/diff":
        return git.diff(await git.checkRoot(message.root), message.scope, message.commit ?? null);
      case "git/log":
        return git.log(await git.checkRoot(message.root));
      case "git/stage":
        return git.stage(await git.checkRoot(message.root), message.paths);
      case "git/unstage":
        return git.unstage(await git.checkRoot(message.root), message.paths);
      case "git/discard":
        return git.discard(await git.checkRoot(message.root), message.paths);
      case "git/apply":
        return git.apply(await git.checkRoot(message.root), message.patch, { cached: message.cached, reverse: message.reverse, index: message.index });
      case "git/commit":
        return git.commit(await git.checkRoot(message.root), message.message, Boolean(message.all));
      case "git/push": {
        const root = await git.checkRoot(message.root);
        await offline(message.thread, "a push");
        return git.push(root, Boolean(message.force));
      }
      case "git/pull-request": {
        const root = await git.checkRoot(message.root);
        await offline(message.thread, "the forge's pull-request page");
        const url = await git.pullRequestUrl(root);
        await shell.openExternal(url);
        return url;
      }
      case "editors/list":
        editors = findEditors();
        return editors;
      case "editor/open": {
        const root = await git.checkRoot(message.root).catch(() => null);
        const base = root ?? (typeof message.root === "string" && path.isAbsolute(message.root) ? message.root : null);
        if (!base) throw new Error("open a file inside a project");
        const file = path.join(base, git.insidePath(base, message.path));
        const editor = (editors ?? (editors = findEditors())).find((entry) => entry.id === message.editor);
        if (!editor) {
          // The system's choice for the file's type.
          const failed = await shell.openPath(file);
          if (failed) throw new Error(failed);
          return null;
        }
        const { command, args } = openCommand(editor, file, message.line);
        git.launch(command, args, base);
        return null;
      }
      case "prefs/read":
        return prefs.read();
      case "prefs/write":
        return prefs.write(message.values);
      case "window/minimize":
        window?.minimize();
        return null;
      case "window/maximize":
        if (window?.isMaximized()) window.unmaximize();
        else window?.maximize();
        return null;
      case "window/close":
        window?.close();
        return null;
      default:
        throw new Error(`the app window does not send ${(message as { type: string }).type}`);
    }
  }

  // Light only (DESKTOP §14 question 3; dark not approved 2026-10-16): the system theme is not
  // followed, so no nativeTheme change reaches the page or the title bar overlay (work.ts).

  // A second instance, or a `mightling://` link: show what it asked for, in the one window.
  app.on("second-instance", (_event, argv, _cwd, data) => {
    const extra = (data as { argv?: string[] } | undefined)?.argv ?? argv.slice(1);
    const asked = require("./args").workTarget(extra) as WorkTarget | null;
    if (asked) {
      target = asked;
      showView("work");
      deliver({ channel: "work://message", payload: { method: "mightling/target", params: target } });
    } else showView("ask");
  });
  app.on("open-url", (event, url) => {
    event.preventDefault();
    target = require("./args").linkTarget(url) as WorkTarget;
    showView("work");
  });
  app.on("window-all-closed", () => {
    server.stop();
    app.quit();
  });
  app.on("before-quit", () => server.stop());

  showView(options.work ? "work" : "ask");
  if (options.audit !== null) {
    try {
      server.start();
    } catch (error) {
      console.error(`ling-app: ${error instanceof Error ? error.message : String(error)}`);
    }
    setTimeout(() => app.quit(), options.audit * 1000);
  }
}
