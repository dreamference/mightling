// The windows and the one IPC channel between Work's page and this process
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3). Every message from the page is checked to
// come from Work's own window before it is acted on: Chat is Onyx's page and has no business with
// the agent, and it has no preload to send anything anyway.

import path from "node:path";
import { BrowserWindow, app, ipcMain, nativeTheme, session, type IpcMainInvokeEvent } from "electron";

import { CHANNEL_CHUNK_ACK, CHANNEL_FOR_VIEW, CHANNEL_FROM_VIEW, Reassembler, chunked, isChunk, type ForView, type FromView } from "./api";
import { airgapped } from "./airgapped";
import { serve } from "./app-protocol";
import type { WorkTarget } from "./args";
import { findLing } from "./bridge";
import { openChat } from "./chat";
import { AppServer } from "./server";
import { installMenu, installTray, keepAwake, notifyTurnDone, showContextMenu } from "./shell";
import { openWork } from "./work";

export interface MainOptions {
  /** The forwarder's port on a client; `null` on a node. */
  port: number | null;
  /** What `ling app` asked for: Work with a folder or thread, or `null` for Chat. */
  work: WorkTarget | null;
  /** The audit's session: hidden windows, Chat and Work both opened, quit after this many seconds. */
  audit: number | null;
}

const resourcesPath = () => (app.isPackaged ? process.resourcesPath : null);

export async function main(options: MainOptions): Promise<void> {
  // Work's page is the built renderer beside this bundle, packaged or not: app:// serves files,
  // never Vite's dev server.
  serve(path.join(__dirname, "..", "renderer", "main_window"));

  let chat: BrowserWindow | null = null;
  let work: BrowserWindow | null = null;
  let target: WorkTarget = options.work ?? { cwd: null, thread: null };
  // Hidden for the audit's session and for an end-to-end test (`MIGHTLING_HIDDEN=1`).
  const show = options.audit === null && !process.env.MIGHTLING_HIDDEN;

  const deliver = (message: ForView) => {
    const contents = work?.webContents;
    if (!contents || contents.isDestroyed()) return;
    for (const piece of chunked(message)) contents.send(CHANNEL_FOR_VIEW, piece);
  };
  const server = new AppServer(resourcesPath(), {
    deliver,
    busyChanged: (busy) => {
      keepAwake(busy);
      if (!busy) notifyTurnDone(work, "The agent finished a turn.");
    },
  });

  const showChat = () => {
    if (chat && !chat.isDestroyed()) {
      chat.show();
      chat.focus();
      return;
    }
    // Chromium keeps Onyx's immutable stylesheets; empty the cache so the last `chat configure` shows.
    void session.defaultSession.clearCache();
    chat = openChat({ port: options.port, show });
    chat.on("closed", () => (chat = null));
  };
  const showWork = () => {
    if (work && !work.isDestroyed()) {
      work.show();
      work.focus();
      return;
    }
    work = openWork({ preload: path.join(__dirname, "preload.js"), show });
    work.on("closed", () => (work = null));
  };

  installMenu({ openChat: showChat, openWork: showWork });
  // `.vite/build/../../icons`: inside the asar when packaged, the project's folder otherwise.
  installTray(path.join(__dirname, "..", "..", "icons"), { openChat: showChat, openWork: showWork });

  // The one channel from Work's page.
  const fromView = new Reassembler();
  ipcMain.handle(CHANNEL_FROM_VIEW, async (event: IpcMainInvokeEvent, data: unknown) => {
    if (!work || event.sender.id !== work.webContents.id) throw new Error("only the Work window talks to the agent");
    let message = data;
    if (isChunk(data)) {
      message = fromView.take(data);
      if (message === undefined) return null;
    }
    return handle(message as FromView);
  });
  ipcMain.on(CHANNEL_CHUNK_ACK, () => {});

  async function handle(message: FromView): Promise<unknown> {
    switch (message.type) {
      case "work/start":
        return server.start();
      case "work/send":
        server.send(message.message);
        return null;
      case "work/stop":
        server.stop();
        return null;
      case "work/open-chat":
        showChat();
        return null;
      case "work/target":
        return target;
      case "work/airgapped": {
        const ling = findLing(resourcesPath());
        return ling ? airgapped(ling, message.thread) : { level: "off", source: "ling is not installed" };
      }
      case "context-menu":
        if (work) showContextMenu(work, message);
        return null;
      case "window/minimize":
        work?.minimize();
        return null;
      case "window/maximize":
        if (work?.isMaximized()) work.unmaximize();
        else work?.maximize();
        return null;
      case "window/close":
        work?.close();
        return null;
      default:
        throw new Error(`the Work window does not send ${(message as { type: string }).type}`);
    }
  }

  nativeTheme.on("updated", () => deliver({ channel: "theme", payload: nativeTheme.shouldUseDarkColors ? "dark" : "light" }));

  // A second instance, or a `mightling://` link: show what it asked for.
  app.on("second-instance", (_event, argv, _cwd, data) => {
    const extra = (data as { argv?: string[] } | undefined)?.argv ?? argv.slice(1);
    const asked = require("./args").workTarget(extra) as WorkTarget | null;
    if (asked) {
      target = asked;
      showWork();
      deliver({ channel: "work://message", payload: { method: "mightling/target", params: target } });
    } else showChat();
  });
  app.on("open-url", (event, url) => {
    event.preventDefault();
    target = require("./args").linkTarget(url) as WorkTarget;
    showWork();
  });
  app.on("window-all-closed", () => {
    server.stop();
    app.quit();
  });
  app.on("before-quit", () => server.stop());

  if (options.audit !== null) {
    showChat();
    showWork();
    try {
      server.start();
    } catch (error) {
      console.error(`ling-app: ${error instanceof Error ? error.message : String(error)}`);
    }
    setTimeout(() => app.quit(), options.audit * 1000);
    return;
  }
  if (options.work) showWork();
  else showChat();
}
