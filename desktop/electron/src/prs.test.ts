// The pull-request tab's `gh` (DESKTOP_PARITY Phase 3 item 9): what it is run with, and what is refused before it runs.
import { describe, expect, it } from "vitest";

import { ghEnv, prNumber, review } from "./prs";

describe("the pull-request tab's gh", () => {
  it("runs without prompts, pager or update check", () => {
    const env = ghEnv({ PATH: "/usr/bin" });
    expect(env).toMatchObject({ PATH: "/usr/bin", GH_PROMPT_DISABLED: "1", GH_NO_UPDATE_NOTIFIER: "1", GH_PAGER: "cat", NO_COLOR: "1" });
  });

  it("takes a pull request number and nothing else", () => {
    expect(prNumber(12)).toBe("12");
    expect(prNumber("7")).toBe("7");
    for (const bad of [0, -1, 1.5, "12; rm -rf /", "--web", null]) expect(() => prNumber(bad)).toThrow();
  });

  it("refuses an empty comment or change request, and verdicts it does not know, before running anything", async () => {
    await expect(review("/nowhere", 3, "comment", " ")).rejects.toThrow("needs words");
    await expect(review("/nowhere", 3, "merge" as never, "x")).rejects.toThrow("not a review verdict");
  });
});
