// Scheduled's words for whether Night Shift runs by itself (DESKTOP §16 B5).
import { describe, expect, it } from "vitest";

import { timerWords } from "./ScheduledView";

describe("the Night Shift timer in words", () => {
  it("says on with its window, off with what to do, or that there is no timer", () => {
    expect(timerWords({ timer: "on", window: "01:00-07:00" })).toBe("Night Shift is on: it works through the queue every night, 01:00-07:00.");
    expect(timerWords({ timer: "on", window: null })).toBe("Night Shift is on: it works through the queue every night.");
    expect(timerWords({ timer: "off", window: null })).toContain("ling-admin night enable");
    expect(timerWords({ timer: "unsupported", window: null })).toContain("no timer");
  });
});
