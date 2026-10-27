// Subagent marks (DESKTOP §16 B21): one per id, the same everywhere, spread out.
import { describe, expect, it } from "vitest";

import { agentMark, GLYPHS, HUES } from "./agentmarks";

describe("subagent marks", () => {
  it("are the same for the same id and differ across ids", () => {
    expect(agentMark("019a-agent-1")).toEqual(agentMark("019a-agent-1"));
    const marks = new Set(Array.from({ length: 40 }, (_, index) => JSON.stringify(agentMark(`agent-${index}`))));
    expect(marks.size).toBeGreaterThan(20);
    const mark = agentMark("x");
    expect(GLYPHS).toContain(mark.glyph);
    expect(HUES).toContain(mark.hue);
  });
});
