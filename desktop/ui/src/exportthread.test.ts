// A thread exported to a file (the owner's decision 6): messages and changed files, redacted by default.
import { describe, expect, it } from "vitest";

import { DEFAULT_EXPORT, exportName, redact, threadHtml, threadMarkdown, type ExportSource } from "./exportthread";

const source: ExportSource = {
  title: "Fix the flaky retry test",
  cwd: "/home/user/src/fetcher",
  home: "/home/user",
  turns: [{
    items: [
      { type: "userMessage", id: "u1", content: [{ type: "text", text: "Why does /home/user/src/fetcher/tests/test_fetch.py fail? Ask someone@example.org.", text_elements: [] }, { type: "text", text: "<project_context>\nsecret instructions\n</project_context>", text_elements: [] }] },
      { type: "reasoning", id: "r1", summary: ["Looking at the retry loop."], content: [] },
      { type: "commandExecution", id: "c1", command: "pytest -q", cwd: "/home/user/src/fetcher", processId: null, status: "completed", commandActions: [], exitCode: 1, durationMs: 10, aggregatedOutput: "FAILED with OPENAI_API_KEY=sk-proj-abcdefghijklmnop1234" },
      { type: "fileChange", id: "f1", status: "completed", changes: [{ path: "fetcher/client.py", kind: { type: "update", move_path: null }, diff: "@@ -1 +1 @@\n-a\n+b" }] },
      { type: "agentMessage", id: "m1", text: "Fixed in `/opt/tools/bin/runner` and /home/user/notes.txt." },
    ],
  } as never],
};

describe("a thread's export", () => {
  it("holds the messages and the changed files, redacted, without commands, thinking or the project context by default", () => {
    const text = threadMarkdown(source, DEFAULT_EXPORT);
    expect(text).toContain("# Fix the flaky retry test");
    expect(text).toContain("Why does ./tests/test_fetch.py fail? Ask [email].");
    expect(text).toContain("**Changed `fetcher/client.py`**");
    expect(text).toContain("```diff\n@@ -1 +1 @@\n-a\n+b\n```");
    expect(text).toContain("Fixed in `…/runner` and ~/notes.txt.");
    for (const absent of ["/home/user", "someone@example.org", "secret instructions", "pytest -q", "Looking at the retry loop"]) expect(text).not.toContain(absent);
  });

  it("adds commands and thinking when asked, still masking tokens, and keeps everything when nothing is redacted", () => {
    const text = threadMarkdown(source, { ...DEFAULT_EXPORT, includeCommands: true, includeReasoning: true });
    expect(text).toContain("**Ran** `pytest -q` (exit 1)");
    expect(text).toContain("OPENAI_API_KEY=[masked]");
    expect(text).toContain("> **Thinking:** Looking at the retry loop.");
    const plain = threadMarkdown(source, { ...DEFAULT_EXPORT, redactPaths: false, redactEmails: false, redactTokens: false, includeCommands: true });
    expect(plain).toContain("/home/user/src/fetcher/tests/test_fetch.py");
    expect(plain).toContain("someone@example.org");
    expect(plain).toContain("sk-proj-abcdefghijklmnop1234");
  });

  it("is a self-contained web page with no script, and a safe file name", () => {
    const html = threadHtml(source, { ...DEFAULT_EXPORT, format: "html" });
    expect(html).toContain("<title>Fix the flaky retry test</title>");
    expect(html).toContain("default-src 'none'");
    expect(html).not.toMatch(/<script/i);
    expect(exportName("Fix: the flaky / retry test?", "markdown")).toBe("Fix-the-flaky-retry-test.md");
    expect(exportName("", "html")).toBe("thread.html");
    expect(redact("C:\\Users\\user\\proj\\a.txt", DEFAULT_EXPORT, { cwd: "/x", home: null })).toBe("…/a.txt");
  });
});
