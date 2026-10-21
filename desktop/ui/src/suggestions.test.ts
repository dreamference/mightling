// Suggested prompts (DESKTOP_PARITY Phase 3 item 7): what the model is asked and how its answer is read.
import { describe, expect, it } from "vitest";

import { acceptsPrediction, parseSuggestions, suggestionPrompt } from "./suggestions";

describe("suggested prompts", () => {
  it("reads three clean lines from whatever shape the model answers in", () => {
    expect(parseSuggestions("1. Add a test for the retry limit\n2) Log the URL on every retry\n- \"Run the whole suite\"\n4. One more")).toEqual([
      "Add a test for the retry limit", "Log the URL on every retry", "Run the whole suite",
    ]);
    expect(parseSuggestions("```\nAdd a test\nadd a test\n\n```")).toEqual(["Add a test"]);
    expect(parseSuggestions(`${"x".repeat(200)}\nShort one`)).toEqual(["Short one"]);
  });

  it("takes a prediction with Tab or → in the empty composer, and only there (DESKTOP_PARITY_CLAUDE §4.6)", () => {
    const key = (name: string, extra: Partial<{ shiftKey: boolean; ctrlKey: boolean; altKey: boolean; metaKey: boolean }> = {}) => ({ key: name, shiftKey: false, ctrlKey: false, altKey: false, metaKey: false, ...extra });
    expect(acceptsPrediction(key("Tab"), "", "Add a test")).toBe(true);
    expect(acceptsPrediction(key("ArrowRight"), "", "Add a test")).toBe(true);
    expect(acceptsPrediction(key("Tab"), "a", "Add a test")).toBe(false);
    expect(acceptsPrediction(key("Tab"), "", null)).toBe(false);
    expect(acceptsPrediction(key("Tab", { shiftKey: true }), "", "Add a test")).toBe(false);
    expect(acceptsPrediction(key("Enter"), "", "Add a test")).toBe(false);
  });

  it("asks for one message, with the request and the answer, cut short", () => {
    expect(suggestionPrompt("x", "y")).toContain("the one message the user is most likely to send next");
    const prompt = suggestionPrompt("Fix the retry test", "a".repeat(5_000));
    expect(prompt).toContain("The request:\nFix the retry test");
    expect(prompt).toContain(" …");
    expect(prompt.length).toBeLessThan(5_000);
  });
});
