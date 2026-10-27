// A repeating task's cadence, checked in the main process (DESKTOP §16 B42).
import { describe, expect, it } from "vitest";

import { cadenceArgs, parseDays, wholeNumber } from "./cadence";

describe("parseDays", () => {
  it("takes the days as a list or comma-separated, lower case, each once, in the order given", () => {
    expect(parseDays(["mon", "WED", "Fri"])).toEqual(["mon", "wed", "fri"]);
    expect(parseDays(" fri, mon ,fri ")).toEqual(["fri", "mon"]);
  });

  it("refuses no day, an unknown day and anything not text", () => {
    for (const bad of [[], "", " , ", null, undefined, 3, { mon: true }, ["mon", 1], ["mon", "funday"], "monday", "mon;wed", "--test"]) {
      expect(() => parseDays(bad), JSON.stringify(bad)).toThrow("choose the days");
    }
  });
});

describe("wholeNumber", () => {
  it("takes a number or its digits within the range", () => {
    expect(wholeNumber(1, 1, 365, "n")).toBe(1);
    expect(wholeNumber(365, 1, 365, "n")).toBe(365);
    expect(wholeNumber(" 14 ", 1, 365, "n")).toBe(14);
  });

  it("refuses what is out of range, not whole, or not a number or its digits", () => {
    for (const bad of [0, -1, 366, 1.5, NaN, Infinity, "", "abc", "3.0", "1e2", "-3", "0x10", true, null, undefined, [3], { n: 3 }]) {
      expect(() => wholeNumber(bad, 1, 365, "the nights between runs"), String(bad)).toThrow("the nights between runs is a whole number from 1 to 365");
    }
  });
});

describe("cadenceArgs", () => {
  it("gives each cadence's flags", () => {
    expect(cadenceArgs("daily", null)).toEqual(["--daily"]);
    expect(cadenceArgs("weekly", { weekday: "fri" })).toEqual(["--weekly", "fri"]);
    expect(cadenceArgs("days", { days: ["mon", "wed", "fri"] })).toEqual(["--days", "mon,wed,fri"]);
    expect(cadenceArgs("every", { every: 3 })).toEqual(["--every", "3"]);
    expect(cadenceArgs("monthly", { day: "31" })).toEqual(["--monthly", "31"]);
  });

  it("reads only the cadence's own parameter", () => {
    expect(() => cadenceArgs("every", { day: 3 })).toThrow("the nights between runs");
    expect(() => cadenceArgs("monthly", { every: 3 })).toThrow("the day of the month");
    expect(() => cadenceArgs("days", { weekday: "mon" })).toThrow("choose the days");
  });

  it("refuses an unknown cadence, a bad weekly day and options that are not an object", () => {
    expect(() => cadenceArgs("hourly", {})).toThrow("repeat every night");
    expect(() => cadenceArgs(undefined, {})).toThrow("repeat every night");
    expect(() => cadenceArgs("weekly", { weekday: "Fri" })).toThrow("a weekly task names its day");
    expect(() => cadenceArgs("weekly", "fri")).toThrow("a weekly task names its day");
    expect(() => cadenceArgs("every", [3])).toThrow("the nights between runs");
    expect(() => cadenceArgs("monthly", { day: 32 })).toThrow("from 1 to 31");
  });
});
