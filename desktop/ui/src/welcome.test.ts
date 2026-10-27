// The first-run page shows once, to someone with nothing to come back to (DESKTOP §16 B6).
import { describe, expect, it } from "vitest";

import { showsWelcome } from "./WelcomeDialog";

describe("the first-run page", () => {
  it("shows in a full window to someone with no threads who has not seen it", () => {
    expect(showsWelcome(false, 0, true)).toBe(true);
    expect(showsWelcome(true, 0, true)).toBe(false);
    expect(showsWelcome(false, 3, true)).toBe(false);
    expect(showsWelcome(false, 0, false)).toBe(false);
  });
});
