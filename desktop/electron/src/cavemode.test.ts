import { describe, expect, it } from "vitest";

import { changeArgs } from "./cavemode";

describe("cavemode", () => {
  it("turns a change into ling cavemode's arguments", () => {
    expect(changeArgs("Full", null)).toEqual(["cavemode", "default", "full"]);
    expect(changeArgs("built-in", null)).toEqual(["cavemode", "default", "built-in"]);
    expect(changeArgs("off", "019a-b2")).toEqual(["cavemode", "--thread", "019a-b2", "off"]);
  });

  it("refuses an unknown level or a thread id that is not one", () => {
    expect(() => changeArgs("loud", null)).toThrow();
    expect(() => changeArgs("built-in", "019a")).toThrow();
    expect(() => changeArgs("lite", "../../etc")).toThrow();
  });
});
