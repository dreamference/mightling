// A side chat that is not kept (DESKTOP §16 B37): its thread, its first message, what its pane shows, its saved copy.
import { describe, expect, it } from "vitest";

import { firstMessage, keptMessage, keptName, sideChatParams, sideMessages, withoutRecap } from "./sidechatstate";
import { threadContext } from "./tonight";

const view = (texts: [string, string][]) => ({
  thread: {}, activeTurnId: null, usage: null,
  turns: [{ id: "t", status: "completed", items: texts.map(([kind, text], index) => (kind === "user"
    ? { type: "userMessage", id: `u${index}`, content: [{ type: "text", text, text_elements: [] }] }
    : { type: "agentMessage", id: `a${index}`, text })) }],
}) as never;

describe("the side chat", () => {
  it("starts unkept, with no folder of its own: the thread's, read-only, never waiting for an approval", () => {
    const parent = { id: "w1", cwd: "/src/fetcher" } as never;
    expect(sideChatParams(parent, "qwen")).toEqual({ cwd: "/src/fetcher", model: "qwen", ephemeral: true, sandbox: "read-only", approvalPolicy: "never" });
  });

  it("carries the thread's recap in its first message, and shows the user's words without it", () => {
    const recap = threadContext(view([["user", "Why is the test flaky?"], ["agent", "A race in the retry."]]));
    const first = firstMessage(" What is left? ", "Flaky test", recap);
    expect(first).toBe('What is left?\n\nContext: this was asked in the conversation "Flaky test". Its latest messages, oldest first:\nUser: Why is the test flaky?\nAgent: A race in the retry.');
    expect(firstMessage("What is left?", "Empty", [])).toBe("What is left?");
    expect(withoutRecap(first)).toBe("What is left?");
    const chat = view([["user", first], ["agent", "Only the docs."]]);
    expect(sideMessages(chat)).toEqual([{ id: "u0", role: "user", text: "What is left?" }, { id: "a1", role: "agent", text: "Only the docs." }]);
    expect(sideMessages(chat, false)[0].text).toBe(first);
    expect(sideMessages(null)).toEqual([]);
  });

  it("is kept as a saved thread whose first message is what was said, bounded, the first message always kept", () => {
    const kept = keptMessage("Flaky test", [{ id: "u", role: "user", text: "What is left?" }, { id: "a", role: "agent", text: "Only the docs." }]);
    expect(kept).toContain('side chat beside the conversation "Flaky test"');
    expect(kept).toContain("User: What is left?\n\nAgent: Only the docs.");
    const long = Array.from({ length: 12 }, (_, index) => ({ id: `m${index}`, role: index % 2 ? "agent" : "user", text: `${index} ${"x".repeat(5000)}` }) as const);
    const cut = keptMessage("T", long);
    expect(cut.length).toBeLessThan(17_000);
    expect(cut).toContain("User: 0 ");
    expect(cut).toContain("Agent: 11 ");
    expect(cut).not.toContain("Agent: 1 ");
    expect(keptName("x".repeat(200))).toHaveLength(120);
    expect(keptName("Flaky test")).toBe("Side chat: Flaky test");
  });
});
