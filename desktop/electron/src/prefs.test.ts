// The window's preferences file: defaults, a refused key or value, and a write that keeps the rest.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { Prefs, SCHEMA, invalid, withDefaults } from "./prefs";

const scratch = () => fs.mkdtempSync(path.join(os.tmpdir(), "ling-prefs-"));

describe("the window's preferences", () => {
  it("start from the schema's defaults", () => {
    const prefs = new Prefs(scratch());
    expect(prefs.read()).toEqual(Object.fromEntries(Object.entries(SCHEMA).map(([key, spec]) => [key, spec.default])));
    expect(prefs.read().enterBehaviour).toBe("enter");
    expect(prefs.read().followUp).toBe("steer");
  });

  it("write some keys and keep the others, in a file only the user reads", () => {
    const folder = scratch();
    const prefs = new Prefs(folder);
    prefs.write({ followUp: "queue" });
    prefs.write({ recentModels: ["qwen3.8-27b"] });
    expect(prefs.read()).toMatchObject({ followUp: "queue", recentModels: ["qwen3.8-27b"], enterBehaviour: "enter" });
    if (process.platform !== "win32") expect(fs.statSync(path.join(folder, "prefs.json")).mode & 0o777).toBe(0o600);
  });

  it("refuse a key the schema does not have, or a value of the wrong shape, and write nothing", () => {
    const prefs = new Prefs(scratch());
    expect(() => prefs.write({ followUp: "queue", ghost: 1 })).toThrow("there is no preference named ghost");
    expect(() => prefs.write({ followUp: "later" })).toThrow("followUp must be one of steer, queue");
    expect(() => prefs.write({ recentModels: [1] })).toThrow("recentModels must be a list of strings");
    expect(() => prefs.write("followUp=queue")).toThrow("preferences must be an object");
    expect(prefs.read().followUp).toBe("steer");
    expect(invalid("__proto__", {})).toBe("there is no preference named __proto__");
  });

  it("ignore a damaged or hand-edited file's bad entries", () => {
    const folder = scratch();
    fs.writeFileSync(path.join(folder, "prefs.json"), JSON.stringify({ followUp: "queue", enterBehaviour: 3, extra: true }));
    expect(new Prefs(folder).read()).toMatchObject({ followUp: "queue", enterBehaviour: "enter" });
    fs.writeFileSync(path.join(folder, "prefs.json"), "{not json");
    expect(new Prefs(folder).read().followUp).toBe("steer");
    expect(withDefaults([1, 2]).followUp).toBe("steer");
  });
});
