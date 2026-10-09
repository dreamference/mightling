// The `app://` scheme that serves Work's UI from inside the asar
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3): registered as privileged before the app is
// ready, as the Codex app registers it, and answered by `protocol.handle` from the bundled renderer
// folder, streaming with the file's MIME type, 404 for anything else. A request from any frame that
// is not the app's own page is cancelled by a session rule.

import { createReadStream, promises as fs } from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { protocol, session } from "electron";

import appConfig from "../app.json";

export const SCHEME = "app";

/** `app://-/<path>`: the host is a placeholder, as in the Codex app. */
export const HOST = "-";

export const WORK_URL = `${SCHEME}://${HOST}/index.html`;

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".wasm": "application/wasm",
  ".map": "application/json",
};

/** Must run before `app.whenReady()`. */
export function registerScheme(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: SCHEME, privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true } },
  ]);
}

/** Serves `root` (the built renderer folder) under `app://-/`; call once the app is ready. */
export function serve(root: string): void {
  const base = path.resolve(root);
  protocol.handle(SCHEME, async (request) => {
    const url = new URL(request.url);
    if (url.host !== HOST) return new Response("not found", { status: 404 });
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, "") || "index.html";
    const file = path.resolve(base, relative);
    if (file !== base && !file.startsWith(base + path.sep)) return new Response("not found", { status: 404 });
    try {
      const stat = await fs.stat(file);
      if (!stat.isFile()) return new Response("not found", { status: 404 });
    } catch {
      return new Response("not found", { status: 404 });
    }
    const headers: Record<string, string> = {
      "Content-Type": MIME[path.extname(file).toLowerCase()] ?? "application/octet-stream",
      "Cache-Control": "no-store",
    };
    if (file.endsWith(".html")) headers["Content-Security-Policy"] = appConfig.csp;
    return new Response(Readable.toWeb(createReadStream(file)) as ReadableStream, { status: 200, headers });
  });

  // Only the app's own page may load `app://` resources: a frame on another origin (a page from
  // anywhere else, were one ever loaded) gets its request cancelled.
  session.defaultSession.webRequest.onBeforeRequest({ urls: [`${SCHEME}://*/*`] }, (details, callback) => {
    // The requesting frame's URL: a top-level navigation has none (`frame` is the frame being
    // navigated), which is the app's own window opening its page.
    const frameUrl = details.resourceType === "mainFrame" ? "" : (details.frame?.url ?? "");
    const foreign = frameUrl !== "" && !frameUrl.startsWith(`${SCHEME}://${HOST}`);
    callback({ cancel: foreign });
  });
}
