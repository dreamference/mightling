import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({ protocol: {}, session: {} }));

import { IMAGE_STORE, imageFile } from "./app-protocol";

describe("images in the app window", () => {
  it("come from the image search store, by the names the service writes and no other", () => {
    expect(IMAGE_STORE.endsWith("/.config/dreamference/image-search/data/images")).toBe(true);
    expect(imageFile("/store", "/images/0123456789abcdef.jpg")).toBe("/store/0123456789abcdef.jpg");
    for (const bad of ["/images/../secret.jpg", "/images/%2e%2e/x.jpg", "/images/0123456789ABCDEF.jpg", "/images/0123456789abcdef.png", "/images/a.jpg", "/index.html"]) {
      expect(imageFile("/store", bad)).toBeNull();
    }
  });
});
