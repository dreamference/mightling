// Terminal tabs in the tab area (DESKTOP §16 B28): their tab, the panel leaving them out, no reopening.
import { describe, expect, it } from "vitest";

import { DEFAULT_PREFS } from "./prefs";
import { surfaceOf, tabLabel, tabbedTerminals, terminalTab, withTab } from "./tabs";

describe("terminal tabs (DESKTOP §16 B28)", () => {
  it("are named after their terminal, are left out of the bottom panel, and are not reopened once closed", () => {
    const tab = terminalTab("pty-7", "Terminal");
    expect(tab.key).toBe("terminal:pty-7");
    expect(tabLabel(tab)).toBe("Terminal");
    expect([...tabbedTerminals(withTab([], tab))]).toEqual(["pty-7"]);
    expect(surfaceOf(tab)).toBeNull();
  });

  it("open in the bottom panel until Settings say otherwise", () => {
    expect(DEFAULT_PREFS.terminalPlace).toBe("panel");
  });
});
