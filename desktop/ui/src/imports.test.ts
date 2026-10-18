// External agent import (DESKTOP_PARITY Phase 3 item 8): what starts ticked, what is refused, how results read.
import { describe, expect, it } from "vitest";

import { refusal, resultLines, scopeOf, startsTicked } from "./imports";

describe("importing another agent's configuration", () => {
  it("ticks what is cheap and local; leaves past sessions to the user; refuses plugins", () => {
    expect(startsTicked({ itemType: "AGENTS_MD" })).toBe(true);
    expect(startsTicked({ itemType: "SKILLS" })).toBe(true);
    expect(startsTicked({ itemType: "SESSIONS" })).toBe(false);
    expect(startsTicked({ itemType: "PLUGINS" })).toBe(false);
    expect(refusal({ itemType: "PLUGINS" })).toContain("marketplaces");
    expect(refusal({ itemType: "MCP_SERVER_CONFIG" })).toBeNull();
  });

  it("says where an item applies and what an import did", () => {
    expect(scopeOf({ cwd: null })).toBe("for every project");
    expect(scopeOf({ cwd: "/home/u/src/fetcher" })).toBe("for fetcher");
    expect(resultLines([
      { itemType: "SKILLS", successes: [{}, {}] as never, failures: [] },
      { itemType: "HOOKS", successes: [], failures: [{ message: "unknown event" }] as never },
    ])).toEqual(["Skills: 2 imported", "Hooks: 0 imported, 1 failed (unknown event)"]);
  });
});
