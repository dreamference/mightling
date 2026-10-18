// Suggested prompts (DESKTOP_PARITY Phase 3 item 7): what the model is asked and how its answer is read.
import { describe, expect, it } from "vitest";

import { parseSuggestions, suggestionPrompt } from "./suggestions";

describe("suggested prompts", () => {
  it("reads three clean lines from whatever shape the model answers in", () => {
    expect(parseSuggestions("1. Add a test for the retry limit\n2) Log the URL on every retry\n- \"Run the whole suite\"\n4. One more")).toEqual([
      "Add a test for the retry limit", "Log the URL on every retry", "Run the whole suite",
    ]);
    expect(parseSuggestions("```\nAdd a test\nadd a test\n\n```")).toEqual(["Add a test"]);
    expect(parseSuggestions(`${"x".repeat(200)}\nShort one`)).toEqual(["Short one"]);
  });

  it("asks with the request and the answer, cut short", () => {
    const prompt = suggestionPrompt("Fix the retry test", "a".repeat(5_000));
    expect(prompt).toContain("The request:\nFix the retry test");
    expect(prompt).toContain(" …");
    expect(prompt.length).toBeLessThan(5_000);
  });
});
