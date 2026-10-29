import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

type Filter = (details: { resourceType: string; frame?: { url: string } | null }, callback: (answer: { cancel: boolean }) => void) => void;

const handlers = vi.hoisted(() => new Map<string, (request: Request) => Promise<Response>>());
const filters = vi.hoisted(() => [] as { urls: string[]; filter: Filter }[]);
const privileged = vi.hoisted(() => [] as unknown[]);
vi.mock("electron", () => ({
  protocol: {
    handle: (scheme: string, handler: (request: Request) => Promise<Response>) => handlers.set(scheme, handler),
    registerSchemesAsPrivileged: (schemes: unknown[]) => privileged.push(...schemes),
  },
  session: { defaultSession: { webRequest: { onBeforeRequest: (match: { urls: string[] }, filter: Filter) => filters.push({ urls: match.urls, filter }) } } },
}));

import appConfig from "../app.json";
import { IMAGE_STORE, SCHEME, WORK_URL, imageFile, registerScheme, serve } from "./app-protocol";

/** A renderer folder with a page and a script, served under `app://-/`. */
function served(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mightling-app-protocol-"));
  fs.writeFileSync(path.join(root, "index.html"), "<!doctype html>");
  fs.writeFileSync(path.join(root, "a.js"), "export {};");
  fs.mkdirSync(path.join(root, "assets"));
  fs.writeFileSync(path.join(path.dirname(root), "mightling-app-protocol-outside.txt"), "outside");
  serve(root);
  return root;
}

describe("images in the app window", () => {
  it("come from the image search store, by the names the service writes and no other", () => {
    expect(IMAGE_STORE.endsWith(path.join(".config", "dreamference", "image-search", "data", "images"))).toBe(true);
    expect(imageFile("/store", "/images/0123456789abcdef.jpg")).toBe(path.join("/store", "0123456789abcdef.jpg"));
    for (const bad of ["/images/../secret.jpg", "/images/%2e%2e/x.jpg", "/images/0123456789ABCDEF.jpg", "/images/0123456789abcdef.png", "/images/a.jpg", "/index.html"]) {
      expect(imageFile("/store", bad)).toBeNull();
    }
  });
});

describe("the app's own files", () => {
  it("answer a malformed escape in the address with not found", async () => {
    serve(path.join(path.sep, "no-such-renderer"));
    const response = await handlers.get(SCHEME)!(new Request("app://-/%E0%A4"));
    expect(response.status).toBe(404);
  });

  it("stream with their MIME type, never cached, and only the page carries the CSP", async () => {
    const root = served();
    try {
      const page = await handlers.get(SCHEME)!(new Request(WORK_URL));
      expect(page.status).toBe(200);
      expect(page.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
      expect(page.headers.get("Cache-Control")).toBe("no-store");
      expect(page.headers.get("Content-Security-Policy")).toBe(appConfig.csp);
      expect(await page.text()).toBe("<!doctype html>");
      const script = await handlers.get(SCHEME)!(new Request("app://-/a.js"));
      expect(script.headers.get("Content-Type")).toBe("text/javascript; charset=utf-8");
      expect(script.headers.get("Content-Security-Policy")).toBeNull();
      expect((await handlers.get(SCHEME)!(new Request("app://-/"))).headers.get("Content-Type")).toBe("text/html; charset=utf-8");
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("answer anything else with not found: another host, a missing file, a folder, a way out of the folder", async () => {
    const root = served();
    try {
      for (const url of ["app://other/index.html", "app://-/missing.js", "app://-/assets", "app://-/..%2Fmightling-app-protocol-outside.txt", "app://-/%2E%2E/mightling-app-protocol-outside.txt"]) {
        expect((await handlers.get(SCHEME)!(new Request(url))).status, url).toBe(404);
      }
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
      fs.rmSync(path.join(path.dirname(root), "mightling-app-protocol-outside.txt"), { force: true });
    }
  });
});

describe("the app:// scheme", () => {
  it("is registered privileged: standard, secure, streaming and open to fetch", () => {
    registerScheme();
    expect(privileged).toEqual([{ scheme: "app", privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true } }]);
  });

  it("is loaded by the app's own page alone: a request from a frame on another origin is cancelled", () => {
    serve(path.join(path.sep, "no-such-renderer"));
    const { urls, filter } = filters[filters.length - 1];
    expect(urls).toEqual(["app://*/*"]);
    const cancelled = (details: Parameters<Filter>[0]) => {
      let answer: boolean | null = null;
      filter(details, ({ cancel }) => (answer = cancel));
      return answer;
    };
    // The window opening its page: a top-level navigation names the frame it navigates, not who asked.
    expect(cancelled({ resourceType: "mainFrame", frame: { url: "https://example.com/" } })).toBe(false);
    expect(cancelled({ resourceType: "script", frame: { url: "app://-/index.html" } })).toBe(false);
    expect(cancelled({ resourceType: "script", frame: null })).toBe(false);
    expect(cancelled({ resourceType: "script", frame: { url: "https://example.com/" } })).toBe(true);
    expect(cancelled({ resourceType: "subFrame", frame: { url: "http://localhost:3100/" } })).toBe(true);
    expect(cancelled({ resourceType: "image", frame: { url: "app://other/index.html" } })).toBe(true);
  });
});
