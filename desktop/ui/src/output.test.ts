// Tests for the full-screen read-only command output view and its pure search helper (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §10 G8).
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  copyOutputText,
  findCommandItem,
  findMatches,
  formatMatchCount,
  OutputView,
  stepMatchIndex,
} from "./OutputView";
import type { Thread } from "./protocol/v2/Thread";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import { initialState, reduce } from "./store";

function makeCommandItem(id: string, command: string, aggregatedOutput: string | null): Extract<ThreadItem, { type: "commandExecution" }> {
  return {
    type: "commandExecution",
    id,
    command,
    cwd: "/home/u/chat-repos/app",
    processId: null,
    source: "unifiedExecStartup",
    status: "inProgress",
    commandActions: [],
    aggregatedOutput,
    exitCode: null,
    durationMs: null,
  } as unknown as Extract<ThreadItem, { type: "commandExecution" }>;
}

function makeThread(id: string, item: ThreadItem): Thread {
  return {
    id,
    preview: "Run tests",
    ephemeral: false,
    modelProvider: "openai-custom",
    createdAt: 1,
    updatedAt: 1,
    status: { type: "active", activeFlags: [] },
    path: null,
    cwd: "/home/u/chat-repos/app",
    cliVersion: "0.158.0",
    source: "appServer",
    agentNickname: null,
    agentRole: null,
    gitInfo: null,
    name: "Test run",
    turns: [{ id: "turn-1", status: "inProgress", error: null, items: [item] }],
  } as unknown as Thread;
}

describe("findMatches (G8)", () => {
  it("returns no ranges when the query is empty or the text is empty", () => {
    expect(findMatches("line one\nline two", "")).toEqual([]);
    expect(findMatches("", "line")).toEqual([]);
  });

  it("finds all case-insensitive non-overlapping ranges in order", () => {
    const text = "Error: failed to compile\nerror: test failed\nERROR: done";
    expect(findMatches(text, "error")).toEqual([
      { start: 0, end: 5 },
      { start: 25, end: 30 },
      { start: 44, end: 49 },
    ]);
    expect(findMatches("aaaa", "aa")).toEqual([
      { start: 0, end: 2 },
      { start: 2, end: 4 },
    ]);
  });

  it("steps between matches with wrap-around and formats the match count", () => {
    expect(stepMatchIndex(0, 0, 1)).toBe(0);
    expect(stepMatchIndex(0, 17, -1)).toBe(16);
    expect(stepMatchIndex(1, 17, 1)).toBe(2);
    expect(stepMatchIndex(16, 17, 1)).toBe(0);
    expect(formatMatchCount(2, 17, "err")).toBe("3 of 17");
    expect(formatMatchCount(0, 0, "missing")).toBe("0 of 0");
    expect(formatMatchCount(0, 5, "")).toBe("");
  });
});

describe("OutputView (G8)", () => {
  it("reads the command item live from the store by threadId and itemId as deltas stream in", () => {
    const item = makeCommandItem("cmd-1", "pytest -q", "collecting ...");
    const thread = makeThread("thread-1", item);
    let state = reduce(initialState, { type: "opened", thread });

    expect(findCommandItem(state.threads, "thread-1", "cmd-1")?.aggregatedOutput).toBe("collecting ...");

    const firstHtml = renderToStaticMarkup(
      createElement(OutputView, {
        threads: state.threads,
        threadId: "thread-1",
        itemId: "cmd-1",
        onClose: () => {},
      }),
    );
    expect(firstHtml).toContain("pytest -q");
    expect(firstHtml).toContain("collecting ...");

    // Stream an output delta into the store without changing the OutputView props other than state.threads.
    state = reduce(state, {
      type: "notification",
      notification: {
        method: "item/commandExecution/outputDelta",
        params: {
          threadId: "thread-1",
          turnId: "turn-1",
          itemId: "cmd-1",
          delta: "\n12 passed in 0.41s",
        },
      },
    });

    const updatedHtml = renderToStaticMarkup(
      createElement(OutputView, {
        threads: state.threads,
        threadId: "thread-1",
        itemId: "cmd-1",
        onClose: () => {},
      }),
    );
    expect(updatedHtml).toContain("collecting ...\n12 passed in 0.41s");
  });

  it("highlights matches, shows the match counter (3 of 17), and renders Copy and Close controls", () => {
    const repeated = Array.from({ length: 17 }, (_, idx) => `line ${idx + 1}: PASS`).join("\n");
    const thread = makeThread("thread-1", makeCommandItem("cmd-2", "cargo test", repeated));
    const html = renderToStaticMarkup(
      createElement(OutputView, {
        threads: { "thread-1": thread },
        threadId: "thread-1",
        itemId: "cmd-2",
        onClose: () => {},
        initialQuery: "pass",
        initialActiveMatch: 2,
        initialCopyStatus: "Copied",
      }),
    );
    expect(html).toContain("3 of 17");
    expect(html).toContain('<mark data-match-index="2" class="output-match current">PASS</mark>');
    expect(html).toContain("Previous");
    expect(html).toContain("Next");
    expect(html).toContain("Copy");
    expect(html).toContain("Copied");
    expect(html).toContain("Close");
  });

  it("copies output via navigator.clipboard.writeText and reports Copied or the failure message", async () => {
    const writeText = vi.fn(async (_text: string) => {});
    expect(await copyOutputText("hello world", { writeText })).toBe("Copied");
    expect(writeText).toHaveBeenCalledWith("hello world");

    const failing = {
      writeText: vi.fn(async () => {
        throw new Error("Permission denied");
      }),
    };
    expect(await copyOutputText("hello world", failing)).toBe("Copy failed: Permission denied");
    expect(await copyOutputText("hello world", null)).toBe("Copy failed: clipboard unavailable");
  });
});
