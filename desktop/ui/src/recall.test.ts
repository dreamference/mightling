// Earlier questions for the agent (DESKTOP_PARITY_CLAUDE §4.4): what is kept of a question.
import { describe, expect, it } from "vitest";

import { keptQuestions, transcript } from "./recall";
import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";

const thread = (extra: Partial<Thread>) => ({ id: "t1", name: null, preview: "What is NVFP4?", cwd: "/home/u/.mightling/ask/q-1a", createdAt: 1760000000, updatedAt: 1760000600, ephemeral: false, parentThreadId: null, ...extra }) as unknown as Thread;

describe("earlier questions", () => {
  it("keeps the user's Ask questions only: no incognito one, no subagent, no Work thread", () => {
    const ask = (t: Thread) => t.cwd.includes("/ask/");
    const kept = keptQuestions([thread({}), thread({ id: "i", ephemeral: true }), thread({ id: "s", parentThreadId: "t1" }), thread({ id: "w", cwd: "/home/u/src/app" })], ask);
    expect(kept.map((t) => t.id)).toEqual(["t1"]);
  });

  it("writes the title, the dates, the folder, then each question and answer, nothing else", () => {
    const turns = [{ id: "a", status: "completed", error: null, items: [
      { type: "userMessage", id: "u", content: [{ type: "text", text: "What is NVFP4?", text_elements: [] }, { type: "localImage", path: "/x/chart.png" }] },
      { type: "reasoning", id: "r", summary: ["secret thinking"], content: [] },
      { type: "commandExecution", id: "c", command: "ling-search nvfp4" },
      { type: "agentMessage", id: "m", text: "A 4-bit float format [1].\n\n[1] https://developer.nvidia.com/blog/nvfp4/" },
    ] }] as unknown as Turn[];
    const text = transcript(thread({ name: "NVFP4\nexplained" }), turns);
    expect(text).toBe([
      "# NVFP4 explained", "", "Asked 2025-10-09 08:53 UTC, last answered 2025-10-09 09:03 UTC. Folder: /home/u/.mightling/ask/q-1a", "",
      "## Question", "", "What is NVFP4?\n[image: chart.png]", "", "## Answer", "", "A 4-bit float format [1].\n\n[1] https://developer.nvidia.com/blog/nvfp4/", "",
    ].join("\n"));
    expect(text).not.toContain("secret thinking");
    expect(text).not.toContain("ling-search");
  });
});
