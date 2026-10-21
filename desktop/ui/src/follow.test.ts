import { describe, expect, it } from "vitest";

import { NEAR_BOTTOM_PX, nearBottom } from "./follow";

describe("following the conversation", () => {
  it("counts the end, and a line of slack before it, as the bottom", () => {
    expect(nearBottom({ scrollTop: 600, scrollHeight: 1000, clientHeight: 400 })).toBe(true);
    expect(nearBottom({ scrollTop: 600 - NEAR_BOTTOM_PX, scrollHeight: 1000, clientHeight: 400 })).toBe(true);
  });

  it("does not count a reader further up as at the bottom", () => {
    expect(nearBottom({ scrollTop: 600 - NEAR_BOTTOM_PX - 1, scrollHeight: 1000, clientHeight: 400 })).toBe(false);
    expect(nearBottom({ scrollTop: 0, scrollHeight: 1000, clientHeight: 400 })).toBe(false);
  });

  it("counts a conversation shorter than the window as at the bottom", () => {
    expect(nearBottom({ scrollTop: 0, scrollHeight: 300, clientHeight: 400 })).toBe(true);
  });
});
