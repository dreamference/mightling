import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

// The image store is under the home folder, read once at import: a scratch home, set before.
const scratch = vi.hoisted(() => {
  const nodeFs = require("node:fs") as typeof import("node:fs");
  const nodeOs = require("node:os") as typeof import("node:os");
  const nodePath = require("node:path") as typeof import("node:path");
  const home = nodeFs.mkdtempSync(nodePath.join(nodeOs.tmpdir(), "app-protocol-"));
  process.env.HOME = home;
  return { home };
});

const captured = vi.hoisted(() => ({
  handler: null as ((request: { url: string }) => Promise<Response>) | null,
  filter: null as ((details: Record<string, unknown>, callback: (answer: { cancel: boolean }) => void) => void) | null,
}));

vi.mock("electron", () => ({
  protocol: {
    handle: (_scheme: string, handler: (request: { url: string }) => Promise<Response>) => (captured.handler = handler),
  },
  session: {
    defaultSession: {
      webRequest: {
        onBeforeRequest: (_filter: unknown, listener: (details: Record<string, unknown>, callback: (answer: { cancel: boolean }) => void) => void) => (captured.filter = listener),
      },
    },
  },
}));

import appConfig from "../app.json";
import { IMAGE_STORE, imageFile, serve } from "./app-protocol";

describe("images in the app window", () => {
  it("come from the image search store, by the names the service writes and no other", () => {
    expect(IMAGE_STORE.endsWith(path.join(".config", "dreamference", "image-search", "data", "images"))).toBe(true);
    expect(imageFile("/store", "/images/0123456789abcdef.jpg")).toBe(path.join("/store", "0123456789abcdef.jpg"));
    for (const bad of ["/images/../secret.jpg", "/images/%2e%2e/x.jpg", "/images/0123456789ABCDEF.jpg", "/images/0123456789abcdef.png", "/images/a.jpg", "/index.html"]) {
      expect(imageFile("/store", bad)).toBeNull();
    }
  });
});

describe("the app:// scheme", () => {
  // The bundle and, beside it, what it must never serve: a file next to the folder and a sibling
  // folder whose name starts with the bundle's.
  const top = fs.mkdtempSync(path.join(os.tmpdir(), "app-protocol-root-"));
  const root = path.join(top, "renderer");
  fs.mkdirSync(path.join(root, "assets"), { recursive: true });
  fs.writeFileSync(path.join(root, "index.html"), "<!doctype html>");
  fs.writeFileSync(path.join(root, "assets", "app.js"), "export {};");
  fs.writeFileSync(path.join(root, "assets", "blob.bin"), "x");
  fs.writeFileSync(path.join(top, "secret.txt"), "secret");
  fs.mkdirSync(path.join(top, "renderer-other"));
  fs.writeFileSync(path.join(top, "renderer-other", "secret.txt"), "secret");
  serve(root);
  const get = (url: string) => captured.handler!({ url });

  it("serves the bundle with its MIME type, and the page alone with the CSP", async () => {
    for (const url of ["app://-/", "app://-/index.html"]) {
      const page = await get(url);
      expect(page.status, url).toBe(200);
      expect(page.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
      expect(page.headers.get("Content-Security-Policy")).toBe(appConfig.csp);
      expect(await page.text()).toBe("<!doctype html>");
    }
    const script = await get("app://-/assets/app.js");
    expect(script.headers.get("Content-Type")).toBe("text/javascript; charset=utf-8");
    expect(script.headers.get("Content-Security-Policy")).toBeNull();
    expect(script.headers.get("Cache-Control")).toBe("no-store");
    expect((await get("app://-/assets/blob.bin")).headers.get("Content-Type")).toBe("application/octet-stream");
  });

  it("answers 404 for anything outside the bundle, however the path is written", async () => {
    for (const url of [
      "app://-/../secret.txt",
      "app://-/..%2Fsecret.txt",
      "app://-/%2e%2e%2fsecret.txt",
      "app://-/assets/..%2F..%2Fsecret.txt",
      "app://-/..%2Frenderer-other%2Fsecret.txt",
      `app://-/${encodeURIComponent(path.join(top, "secret.txt"))}`,
      "app://-/assets",
      "app://-/missing.js",
      "app://other/index.html",
      "app://-/%00index.html",
    ]) {
      expect((await get(url)).status, url).toBe(404);
    }
  });

  it("answers 404 for a path that does not decode, never an error", async () => {
    for (const url of ["app://-/%E0%A4%A", "app://-/%", "app://-/images/%zz"]) {
      const answer = await get(url).catch((error: unknown) => error);
      expect(answer instanceof Response ? answer.status : String(answer), url).toBe(404);
    }
  });

  it("serves a stored image read-only, never a link out of the store", async () => {
    const store = path.join(scratch.home, ".config", "dreamference", "image-search", "data", "images");
    expect(IMAGE_STORE).toBe(store);
    fs.mkdirSync(store, { recursive: true });
    fs.writeFileSync(path.join(store, "0123456789abcdef.jpg"), "jpeg");
    fs.symlinkSync(path.join(top, "secret.txt"), path.join(store, "fedcba9876543210.jpg"));
    const image = await get("app://-/images/0123456789abcdef.jpg");
    expect(image.status).toBe(200);
    expect(image.headers.get("Content-Type")).toBe("image/jpeg");
    expect(await image.text()).toBe("jpeg");
    for (const url of ["app://-/images/fedcba9876543210.jpg", "app://-/images/0000000000000000.jpg", "app://-/images/..%2F..%2Fsecret.jpg", "app://-/images/"]) {
      expect((await get(url)).status, url).toBe(404);
    }
  });

  it("cancels a request from a frame on another origin, and lets the app's own page through", () => {
    const decide = (details: Record<string, unknown>) => {
      let cancel: boolean | null = null;
      captured.filter!(details, (answer) => (cancel = answer.cancel));
      return cancel;
    };
    // The window opening its page: a top-level navigation, whatever the frame says.
    expect(decide({ url: "app://-/index.html", resourceType: "mainFrame", frame: { url: "https://example.com/" } })).toBe(false);
    expect(decide({ url: "app://-/assets/app.js", resourceType: "script", frame: { url: "app://-/index.html#work" } })).toBe(false);
    expect(decide({ url: "app://-/assets/app.js", resourceType: "script", frame: { url: "https://example.com/" } })).toBe(true);
    expect(decide({ url: "app://-/images/0123456789abcdef.jpg", resourceType: "image", frame: { url: "file:///tmp/page.html" } })).toBe(true);
  });
});
