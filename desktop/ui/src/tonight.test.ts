// "Do this tonight" from a thread (the owner's decision 7): the task carries the thread's latest messages.
import { describe, expect, it } from "vitest";

import { threadContext, withThreadContext } from "./tonight";

const view = (texts: [string, string][]) => ({
  thread: {}, activeTurnId: null, usage: null,
  turns: [{ id: "t", status: "completed", items: texts.map(([kind, text], index) => (kind === "user"
    ? { type: "userMessage", id: `u${index}`, content: [{ type: "text", text, text_elements: [] }] }
    : { type: "agentMessage", id: `a${index}`, text })) }],
}) as never;

describe("tonight from a thread", () => {
  it("carries the latest messages, oldest first, bounded, the project context left out", () => {
    const many = Array.from({ length: 12 }, (_, index) => ["user", `question ${index + 1}`] as [string, string]);
    const context = threadContext(view(many));
    expect(context).toHaveLength(8);
    expect(context[0]).toBe("User: question 5");
    expect(context[7]).toBe("User: question 12");
    expect(threadContext(view([["user", "x".repeat(3000)]]))[0].length).toBeLessThan(1300);
    expect(threadContext(view([["user", "<project_context>\nsecret\n</project_context>"], ["agent", "Done."]]))).toEqual(["Agent: Done."]);
    expect(threadContext(null)).toEqual([]);
  });

  it("puts the context after the task's own words", () => {
    expect(withThreadContext(" Fix the retry ", "Flaky test", ["User: why?", "Agent: a race."])).toBe(
      'Fix the retry\n\nContext: this was asked in the conversation "Flaky test". Its latest messages, oldest first:\nUser: why?\nAgent: a race.');
    expect(withThreadContext("Fix it", "T", [])).toBe("Fix it");
  });
});
