// Diagrams in answers (DESKTOP §16 B15): Mermaid's settings keep a diagram from acting.
import { describe, expect, it } from "vitest";

import { mermaidConfig } from "./diagrams";

describe("diagrams", () => {
  it("are drawn strictly: no HTML labels, no start on load, the theme following the window's", () => {
    expect(mermaidConfig(false)).toMatchObject({ securityLevel: "strict", startOnLoad: false, htmlLabels: false, flowchart: { htmlLabels: false }, theme: "default" });
    expect(mermaidConfig(true).theme).toBe("dark");
  });
});
