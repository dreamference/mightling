import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, describe, expect, it, vi } from "vitest";

const handlers = vi.hoisted(() => new Map<string, (request: Request) => Promise<Response>>());
const electron = vi.hoisted(() => ({
  privileged: [] as unknown[],
  guards: [] as { filter: unknown; guard: (details: { resourceType: string; frame?: { url: string } | null }, callback: (answer: { cancel: boolean }) => void) => void }[],
}));
// The image store sits under the home folder: a temporary one here, so no test reads the machine's.
const home = vi.hoisted(() => ({ dir: "" }));
vi.mock("node:os", async (importOriginal) => {
  const real = await importOriginal<typeof import("node:os") & { default: typeof import("node:os") }>();
  home.dir = real.default.tmpdir() + `/ling-app-protocol-${process.pid}-${Date.now().toString(36)}`;
  return { ...real, default: { ...real.default, homedir: () => home.dir } };
});
vi.mock("electron", () => ({
  protocol: {
    handle: (scheme: string, handler: (request: Request) => Promise<Response>) => handlers.set(scheme, handler),
    registerSchemesAsPrivileged: (schemes: unknown[]) => electron.privileged.push(...schemes),
  },
  session: { defaultSession: { webRequest: { onBeforeRequest: (filter: unknown, guard: (typeof electron.guards)[number]["guard"]) => electron.guards.push({ filter, guard }) } } },
}));

import appConfig from "../app.json";
import { IMAGE_STORE, SCHEME, imageFile, registerScheme, serve } from "./app-protocol";

afterAll(() => rmSync(home.dir, { recursive: true, force: true }));

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
});

describe("the app's own files, served", () => {
  const root = path.join(mkdtempSync(path.join(os.tmpdir(), "ling-renderer-")), "renderer");
  mkdirSync(path.join(root, "sub"), { recursive: true });
  writeFileSync(path.join(root, "index.html"), "<p>page</p>");
  writeFileSync(path.join(root, "app.js"), "export {};");
  writeFileSync(path.join(root, "data.bin"), "bytes");
  // A folder beside the renderer's whose name starts with the renderer's own.
  mkdirSync(`${root}-other`);
  writeFileSync(path.join(`${root}-other`, "secret.txt"), "secret");
  afterAll(() => rmSync(path.dirname(root), { recursive: true, force: true }));
  const get = (url: string) => handlers.get(SCHEME)!(new Request(url));

  it("only from the app's host and from inside the renderer's folder", async () => {
    serve(root);
    expect((await get("app://-/index.html")).status).toBe(200);
    expect((await get("app://other/index.html")).status).toBe(404);
    expect((await get("app://-/..%2frenderer-other%2fsecret.txt")).status).toBe(404);
    expect((await get("app://-/..%2f..%2fetc%2fpasswd")).status).toBe(404);
  });

  it("only files: a folder or a missing name is not found", async () => {
    serve(root);
    expect((await get("app://-/sub")).status).toBe(404);
    expect((await get("app://-/missing.js")).status).toBe(404);
  });

  it("each with its type, never cached, and the page with the CSP", async () => {
    serve(root);
    const page = await get("app://-/");
    expect(await page.text()).toBe("<p>page</p>");
    expect(page.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
    expect(page.headers.get("Cache-Control")).toBe("no-store");
    expect(page.headers.get("Content-Security-Policy")).toBe(appConfig.csp);
    const script = await get("app://-/app.js");
    expect(script.headers.get("Content-Type")).toBe("text/javascript; charset=utf-8");
    expect(script.headers.get("Cache-Control")).toBe("no-store");
    expect(script.headers.get("Content-Security-Policy")).toBeNull();
    await script.text();
    const other = await get("app://-/data.bin");
    expect(other.headers.get("Content-Type")).toBe("application/octet-stream");
    await other.text();
  });

  it("and the stored images, only files in the store, never a link out of it", async () => {
    expect(IMAGE_STORE.startsWith(home.dir + path.sep)).toBe(true);
    mkdirSync(IMAGE_STORE, { recursive: true });
    writeFileSync(path.join(IMAGE_STORE, "0123456789abcdef.jpg"), "jpeg");
    mkdirSync(path.join(IMAGE_STORE, "1111111111111111.jpg"));
    symlinkSync(path.join(`${root}-other`, "secret.txt"), path.join(IMAGE_STORE, "2222222222222222.jpg"));
    serve(root);
    const image = await get("app://-/images/0123456789abcdef.jpg");
    expect(image.status).toBe(200);
    expect(image.headers.get("Content-Type")).toBe("image/jpeg");
    expect(await image.text()).toBe("jpeg");
    for (const name of ["1111111111111111", "2222222222222222", "3333333333333333"]) {
      expect((await get(`app://-/images/${name}.jpg`)).status, name).toBe(404);
    }
  });
});

describe("the app:// scheme", () => {
  it("is privileged as a standard, secure scheme, and never bypasses the CSP", () => {
    electron.privileged.length = 0;
    registerScheme();
    expect(electron.privileged).toEqual([{ scheme: "app", privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true } }]);
  });

  it("is loaded only by the app's own page: a request from a frame elsewhere is cancelled", () => {
    electron.guards.length = 0;
    serve(path.join(path.sep, "no-such-renderer"));
    expect(electron.guards).toHaveLength(1);
    const { filter, guard } = electron.guards[0];
    expect(filter).toEqual({ urls: ["app://*/*"] });
    const cancelled = (resourceType: string, frame?: { url: string } | null) => {
      let answer: { cancel: boolean } | null = null;
      guard({ resourceType, frame }, (given) => (answer = given));
      return answer!.cancel;
    };
    // A window opening its page: the frame being navigated is not the one asking.
    expect(cancelled("mainFrame", { url: "https://example.com/" })).toBe(false);
    expect(cancelled("script", { url: "app://-/index.html" })).toBe(false);
    expect(cancelled("script", null)).toBe(false);
    expect(cancelled("script", { url: "" })).toBe(false);
    expect(cancelled("script", { url: "https://example.com/" })).toBe(true);
    expect(cancelled("image", { url: "app://evil/index.html" })).toBe(true);
    expect(cancelled("subFrame", { url: "file:///etc/passwd" })).toBe(true);
  });
});
