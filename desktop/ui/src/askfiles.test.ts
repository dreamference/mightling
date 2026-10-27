// Files from answers (DESKTOP §16 B30): what a question's folder shows, and which questions are read.
import { describe, expect, it } from "vitest";

import { QUESTIONS_READ, questionsToRead, shownFiles } from "./AskFiles";
import type { Thread } from "./protocol/v2/Thread";

describe("files from answers (DESKTOP §16 B30)", () => {
  it("lists a folder's regular files by name, no hidden ones and no folders", () => {
    expect(shownFiles([
      { fileName: "report.md", isFile: true },
      { fileName: ".incognito", isFile: true },
      { fileName: "chart.png", isFile: true },
      { fileName: "data", isFile: false },
    ])).toEqual(["chart.png", "report.md"]);
  });

  it("reads kept questions only, newest first, capped", () => {
    const thread = (id: string, updatedAt: number, extra: Partial<Thread> = {}) => ({ id, cwd: `/a/${id}`, updatedAt, ephemeral: false, ...extra }) as Thread;
    expect(questionsToRead([thread("old", 1), thread("new", 5), thread("secret", 9, { ephemeral: true })]).map((entry) => entry.id)).toEqual(["new", "old"]);
    expect(questionsToRead(Array.from({ length: 100 }, (_, index) => thread(`q${index}`, index)))).toHaveLength(QUESTIONS_READ);
  });
});
