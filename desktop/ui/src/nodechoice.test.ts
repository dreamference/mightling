import { describe, expect, it } from "vitest";

import type { NodeEntry } from "./bridge";
import { needsNodeChoice, pickable } from "./nodechoice";

describe("the start-up node picker", () => {
  it("asks only when the launcher refused for several nodes", () => {
    expect(needsNodeChoice(["Error: Several Mightling nodes are on this network:", "  1. a"])).toBe(true);
    expect(needsNodeChoice(["the model server stopped"])).toBe(false);
    expect(needsNodeChoice([])).toBe(false);
  });

  it("offers the nodes that can be chosen by a name", () => {
    const node = (name: string, choice: string | null) => ({ name, id: name, choice }) as NodeEntry;
    expect(pickable([node("a", "a"), node("b", null)]).map((n) => n.name)).toEqual(["a"]);
  });
});
