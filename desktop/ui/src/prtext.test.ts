// Create pull request's description (DESKTOP §16 B32) against a scripted stand-in: the instruction from
// Settings › Git (or Mightling's own), the title and the branch's diff go to an ephemeral, read-only thread.
import { describe, expect, it } from "vitest";

import { PR_PROMPT, pullRequestDescription } from "./prtext";
import { ScriptedServer } from "./scripted-server";

const answering = (text: string, seen: string[]) => new ScriptedServer({
  "thread/start": () => ({ thread: { id: "p1" } }),
  "turn/start": (params, self) => {
    seen.push((params.input as { text: string }[])[0].text);
    self.notify("item/completed", { threadId: "p1", turnId: "x", item: { type: "agentMessage", id: "a", text } });
    self.notify("turn/completed", { threadId: "p1", turn: { id: "x", status: "completed", items: [], error: null } });
    return { turn: { id: "x" } };
  },
  "thread/unsubscribe": () => ({}),
});

describe("a pull request's description", () => {
  it("follows the instruction from Settings › Git, with the title and the branch's changes", async () => {
    const seen: string[] = [];
    const server = answering("```\nRetries wait 0.5 s, 1 s, 2 s.\n\n- Tested with `pytest -q`.\n```", seen);
    const text = await pullRequestDescription(server.client, server.listen, "/r", "qwen", "+retry\n", "Retry with backoff", "Start with a Why: line.");
    expect(text).toBe("Retries wait 0.5 s, 1 s, 2 s.\n\n- Tested with `pytest -q`.");
    expect(seen[0]).toBe("Start with a Why: line.\n\nThe pull request's title: Retry with backoff\n\n+retry\n");
    expect(server.sent[0]).toEqual({ method: "thread/start", params: { cwd: "/r", model: "qwen", ephemeral: true, sandbox: "read-only", approvalPolicy: "never" } });
  });

  it("uses Mightling's own instruction when Settings › Git has none, and cuts a long diff", async () => {
    const seen: string[] = [];
    const server = answering("Fine.", seen);
    await pullRequestDescription(server.client, server.listen, "/r", null, "x".repeat(50_000), "");
    expect(seen[0].startsWith(`${PR_PROMPT}\n\nxxx`)).toBe(true);
    expect(seen[0]).toContain("(the rest of the diff is left out)");
    expect(seen[0].length).toBeLessThan(41_000);
  });
});
