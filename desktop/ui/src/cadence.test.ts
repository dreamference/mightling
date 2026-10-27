// A repeating task's cadence in Scheduled (DESKTOP §16 B42).
import { describe, expect, it } from "vitest";

import { cadenceWords, toggleDay } from "./cadence";

const entry = { weekday: null, days: null, every: null, day: null };

describe("cadenceWords", () => {
  it("says each cadence in words", () => {
    expect(cadenceWords({ ...entry, cadence: "daily" })).toBe("every night");
    expect(cadenceWords({ ...entry, cadence: "weekly", weekday: "fri" })).toBe("every Friday");
    expect(cadenceWords({ ...entry, cadence: "days", days: ["mon", "wed", "fri"] })).toBe("every Monday, Wednesday, Friday");
    expect(cadenceWords({ ...entry, cadence: "every", every: 1 })).toBe("every night");
    expect(cadenceWords({ ...entry, cadence: "every", every: 3 })).toBe("every 3 nights");
    expect(cadenceWords({ ...entry, cadence: "monthly", day: 31 })).toBe("on day 31 of each month");
  });
});

describe("toggleDay", () => {
  it("adds or removes a day and keeps the week's order", () => {
    expect(toggleDay(["fri"], "mon")).toEqual(["mon", "fri"]);
    expect(toggleDay(["mon", "wed", "fri"], "wed")).toEqual(["mon", "fri"]);
    expect(toggleDay(["sun"], "sun")).toEqual([]);
  });
});
