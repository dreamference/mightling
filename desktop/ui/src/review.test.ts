// The review pane's server side (DESKTOP_PARITY §2.5) against a scripted stand-in: a commit message
// written by the served model in a throwaway thread. The git half is tested in the main process
// against scratch repositories (desktop/electron/src/git.test.ts).
import { describe, expect, it } from "vitest";

import { ScriptedServer } from "./scripted-server";
import { cleanMessage, commitMessage } from "./turns";

describe("a generated commit message", () => {
  it("comes from an ephemeral, read-only thread that never asks, and the thread is let go after", async () => {
    const server = new ScriptedServer({
      "thread/start": () => ({ thread: { id: "e1" } }),
      "turn/start": (params, self) => {
        self.notify("item/completed", { threadId: "e1", turnId: "x", item: { type: "agentMessage", id: "a", text: "```\nMake the retry loop re-raise\n\nThe last error was swallowed.\n```" } });
        self.notify("turn/completed", { threadId: "e1", turn: { id: "x", status: "completed", items: [], error: null } });
        expect((params.input as { text: string }[])[0].text).toContain("+line 2 changed");
        return { turn: { id: "x" } };
      },
      "thread/unsubscribe": () => ({}),
    });
    const message = await commitMessage(server.client, server.listen, "/r", "qwen", "--- a/a.txt\n+++ b/a.txt\n@@ -2 +2 @@\n-line 2\n+line 2 changed\n");
    expect(message).toBe("Make the retry loop re-raise\n\nThe last error was swallowed.");
    expect(server.sent[0]).toEqual({ method: "thread/start", params: { cwd: "/r", model: "qwen", ephemeral: true, sandbox: "read-only", approvalPolicy: "never" } });
    expect(server.methods()).toEqual(["thread/start", "turn/start", "thread/unsubscribe"]);
  });

  it("fails with the turn's error, and cleans fences and quotes from an answer", async () => {
    const server = new ScriptedServer({
      "thread/start": () => ({ thread: { id: "e2" } }),
      "turn/start": (_params, self) => {
        self.notify("turn/completed", { threadId: "e2", turn: { id: "y", status: "failed", items: [], error: { message: "the model server is busy" } } });
        return { turn: { id: "y" } };
      },
      "thread/unsubscribe": () => ({}),
    });
    await expect(commitMessage(server.client, server.listen, "/r", null, "diff")).rejects.toThrow("the model server is busy");
    expect(cleanMessage('"Fix the build"')).toBe("Fix the build");
  });
});
