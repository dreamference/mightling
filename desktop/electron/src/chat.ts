// The Chat window: the local web UI at `http://localhost:<port>/app`, unchanged since before Work
// existed, now in a Chromium window. No preload, no IPC and no injected script: Onyx's page has no
// business with the agent, and the sign-in happens in this process (`sign-in.ts`), so the page
// never sees the credential.

import { BrowserWindow, net, shell, type Session } from "electron";

import appConfig from "../app.json";
import { WindowSignIn, credentialsFile, readCredentials, type Http, type ParsedCookie } from "./sign-in";

/** The URL the window loads: the configured one, or the same path on the forwarder's fallback port. */
export function chatUrl(port: number | null): string {
  const url = new URL(appConfig.chat.url);
  if (port !== null) url.port = String(port);
  return url.toString();
}

/** The sign-in's network, over `session` (the Chat window's own), with redirects not followed. */
export function sessionHttp(session: Session): Http {
  const request = (method: string, url: string, body?: string) =>
    new Promise<{ status: number; setCookie: string[] }>((resolve, reject) => {
      const outgoing = net.request({ method, url, session, credentials: "include", redirect: "manual" });
      if (body !== undefined) outgoing.setHeader("Content-Type", "application/x-www-form-urlencoded");
      outgoing.on("response", (response) => {
        const header = response.headers["set-cookie"];
        const setCookie = header === undefined ? [] : Array.isArray(header) ? header : [header];
        response.on("data", () => {});
        response.on("end", () => resolve({ status: response.statusCode, setCookie }));
        response.on("error", reject);
      });
      outgoing.on("redirect", () => outgoing.abort());
      outgoing.on("abort", () => reject(new Error("redirected")));
      outgoing.on("error", reject);
      outgoing.end(body);
    });
  return {
    status: async (url) => (await request("GET", url)).status,
    postForm: (url, body) => request("POST", url, body),
    // `credentials: "include"` stores the response's cookies in the session; this covers a
    // Chromium that did not, so the window is signed in either way.
    ensureCookie: async (origin: string, cookie: ParsedCookie) => {
      const existing = await session.cookies.get({ url: origin, name: cookie.name });
      if (existing.length > 0) return;
      await session.cookies.set({ url: origin, ...cookie });
    },
  };
}

export interface ChatOptions {
  port: number | null;
  show: boolean;
}

export function openChat(options: ChatOptions): BrowserWindow {
  const { chat } = appConfig;
  const window = new BrowserWindow({
    title: options.port !== null && options.port !== 3000 ? `${chat.title} (port ${options.port}: 3000 is in use on this machine)` : chat.title,
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
  const url = chatUrl(options.port);
  const origin = new URL(url).origin;
  // Read when needed, never kept: the password lives in this process only for the one request.
  const signIn = new WindowSignIn(origin, () => readCredentials(credentialsFile()), sessionHttp(window.webContents.session));

  // Signed in before the first load where possible, so the login page does not flash; after a
  // load (a server that came up late, a session that expired) the same one attempt is offered.
  const load = () => void window.loadURL(url);
  window.webContents.on("did-finish-load", () => {
    void signIn.run().then((outcome) => {
      if (outcome === "signed-in" && !window.isDestroyed()) load();
    });
  });
  // A link that would open a new tab opens in the system browser, as the Codex app does; the
  // window itself stays on the web UI.
  window.webContents.setWindowOpenHandler(({ url: target }) => {
    if (/^https?:/.test(target)) void shell.openExternal(target);
    return { action: "deny" };
  });
  void signIn.run().finally(load);
  return window;
}

function isPackaged(): boolean {
  try {
    return (require("electron") as typeof import("electron")).app.isPackaged;
  } catch {
    return false;
  }
}
