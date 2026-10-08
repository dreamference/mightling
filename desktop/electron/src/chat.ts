// The Ask window (the menu's former Chat): the Mightling UI served by `ling web` on this machine,
// where Ask threads get the policy layer they need (web.ts). Until 2026-10-08 this window showed
// the Onyx web UI on port 3000; that is still running for anyone who opens it in a browser, but
// the app no longer depends on it (specs/DREAMFERENCE_MIGHTLING_ASK.md §10).
//
// No preload and no IPC: the page talks to `ling web` over its own WebSocket, exactly as in a
// browser, with the session cookie the one-time sign-in link sets. The window never navigates off
// that server; links open in the system browser.

import { BrowserWindow, shell } from "electron";

import appConfig from "../app.json";
import { showContextMenu } from "./shell";
import { sameServer, type WebServer } from "./web";

export interface ChatOptions {
  server: WebServer;
  show: boolean;
}

/** A page shown while `ling web` starts, or when it cannot: text only, nothing loaded. */
export function messagePage(title: string, body: string): string {
  const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const html = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>Mightling</title>` +
    `<style>body{font-family:system-ui,sans-serif;max-width:640px;margin:15vh auto;padding:0 20px;color:#111}p{color:#555;white-space:pre-wrap}</style></head>` +
    `<body><h1>${escape(title)}</h1><p>${escape(body)}</p></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

export function openChat(options: ChatOptions): BrowserWindow {
  const { chat } = appConfig;
  const window = new BrowserWindow({
    title: chat.title,
    width: chat.width,
    height: chat.height,
    minWidth: chat.minWidth,
    minHeight: chat.minHeight,
    backgroundColor: chat.backgroundColor,
    center: true,
    show: options.show,
    autoHideMenuBar: true,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, spellcheck: true, devTools: !isPackaged() },
  });

  let login: string | null = null;
  // One fresh sign-in per refusal: a server restarted since (its sessions live in memory) answers
  // 401 once, and a second refusal in a row is shown rather than retried forever.
  let retried = false;
  const signIn = async () => {
    try {
      login = await options.server.loginUrl();
      if (!window.isDestroyed()) await window.loadURL(login);
    } catch (error) {
      if (window.isDestroyed()) return;
      const reason = error instanceof Error ? error.message : String(error);
      void window.loadURL(messagePage("Mightling could not start Ask", reason));
    }
  };
  window.webContents.on("did-navigate", (_event, url, status) => {
    if (!login || !sameServer(url, login)) return;
    if (status === 401 && !retried) {
      retried = true;
      void signIn();
    } else if (status === 200) {
      retried = false;
    }
  });
  // The page never leaves the server; anything else opens in the system browser.
  window.webContents.on("will-navigate", (event, url) => {
    if (login && sameServer(url, login)) return;
    event.preventDefault();
    if (/^https?:/.test(url)) void shell.openExternal(url);
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("context-menu", (_event, params) => {
    showContextMenu(window, { x: params.x, y: params.y, editable: params.isEditable, selection: params.selectionText });
  });

  void window.loadURL(messagePage("Starting Mightling…", "Starting ling web, the server Ask runs on."));
  void signIn();
  return window;
}

function isPackaged(): boolean {
  try {
    return (require("electron") as typeof import("electron")).app.isPackaged;
  } catch {
    return false;
  }
}
