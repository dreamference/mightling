import path from "node:path";
import { describe, expect, it, vi } from "vitest";

const handlers = vi.hoisted(() => new Map<string, (request: Request) => Promise<Response>>());
vi.mock("electron", () => ({
  protocol: { handle: (scheme: string, handler: (request: Request) => Promise<Response>) => handlers.set(scheme, handler) },
  session: { defaultSession: { webRequest: { onBeforeRequest: () => undefined } } },
}));

import { IMAGE_STORE, SCHEME, imageFile, serve } from "./app-protocol";

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
