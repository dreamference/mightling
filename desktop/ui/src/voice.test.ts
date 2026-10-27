// Settings › Voice's word list (DESKTOP §16 B7): one per line, blank lines and repeats dropped.
import { describe, expect, it } from "vitest";

import { wordsFromText } from "./SettingsDialog";

describe("the dictation word list", () => {
  it("is one word or phrase per line, trimmed, without blanks or repeats", () => {
    expect(wordsFromText("Mightling\n\n  fetcher/client.py \nMightling\n")).toEqual(["Mightling", "fetcher/client.py"]);
    expect(wordsFromText("")).toEqual([]);
  });
});
