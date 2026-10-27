// Summaries of finished chats (DESKTOP §16 B43): which thread is summarized, when, how the answer is read
// and kept, and what goes to the server (a read, then a read-only thread that is not kept).
import { describe, expect, it } from "vitest";

import type { Thread } from "./protocol/v2/Thread";
import { ScriptedServer } from "./scripted-server";
import type { ThreadView } from "./store";
import {
  IDLE_SECONDS, LINE_MAX, SUMMARIES_MAX, SUMMARY_PROMPT, attemptKey, cleanSummaries, nextToSummarize, parseSummary, serverIdle,
  summarize, summaryMatches, summaryOf, summaryPrompt, transcript, withSummary,
} from "./summaries";

const NOW = 2_000_000_000;
const DAY = IDLE_SECONDS;

const thread = (id: string, updatedAt: number, extra: Partial<Thread> = {}): Thread => ({
  id, cwd: "/w/fetcher", name: `Thread ${id}`, preview: "", turns: [], status: { type: "idle" }, ephemeral: false, parentThreadId: null,
  createdAt: updatedAt, updatedAt, ...extra,
} as unknown as Thread);
const view = (t: Thread, activeTurnId: string | null = null): ThreadView => ({ thread: t, turns: [], activeTurnId, usage: null } as unknown as ThreadView);
const text = (value: string) => [{ type: "text", text: value, text_elements: [] }];
const turn = (asked: string, answered: string) => ({ items: [
  { type: "userMessage", id: "u", content: text(asked) },
  { type: "commandExecution", id: "c" },
  { type: "agentMessage", id: "a", text: answered },
] }) as never;

describe("which thread is summarized", () => {
  it("takes the most recent thread idle a day, of the user's own, with no current summary", () => {
    const threads = [
      view(thread("recent", NOW - DAY + 60)),
      view(thread("old", NOW - 3 * DAY)),
      view(thread("older", NOW - 2 * DAY)),
      view(thread("incognito", NOW - 2 * DAY + 5, { ephemeral: true })),
      view(thread("subagent", NOW - 2 * DAY + 5, { parentThreadId: "old" })),
      view(thread("watched", NOW - 2 * DAY + 5, { status: { type: "active", activeFlags: [] } } as Partial<Thread>)),
    ];
    expect(nextToSummarize(threads, {}, NOW)?.id).toBe("older");
    expect(nextToSummarize(threads, { older: { at: NOW - 2 * DAY, text: "a\nb" } }, NOW)?.id).toBe("old");
    // Once each until it changes: a current summary or an attempt at this stamp skips it.
    expect(nextToSummarize(threads, { older: { at: NOW - 2 * DAY, text: "a" }, old: { at: NOW - 3 * DAY, text: "b" } }, NOW)).toBeNull();
    expect(nextToSummarize(threads, {}, NOW, new Set([attemptKey(threads[2].thread), attemptKey(threads[1].thread)]))).toBeNull();
    // A thread that changed since its summary is summarized again.
    expect(nextToSummarize(threads, { older: { at: NOW - 5 * DAY, text: "stale" } }, NOW)?.id).toBe("older");
  });

  it("only among the most recently updated idle threads, as many as are kept", () => {
    const threads = Array.from({ length: SUMMARIES_MAX + 5 }, (_, at) => view(thread(`t${at}`, NOW - DAY - at)));
    const summaries = Object.fromEntries(threads.slice(0, SUMMARIES_MAX).map((entry) => [entry.thread.id, { at: entry.thread.updatedAt, text: "x" }]));
    expect(nextToSummarize(threads, summaries, NOW)).toBeNull();
  });

  it("waits while any turn runs, as suggested prompts do", () => {
    expect(serverIdle([view(thread("a", 1)), view(thread("b", 2))])).toBe(true);
    expect(serverIdle([view(thread("a", 1)), view(thread("b", 2), "turn-1")])).toBe(false);
  });
});

describe("the summaries kept", () => {
  it("shows a summary only while the thread is as it was summarized", () => {
    const summaries = { a: { at: 10, text: "Wanted X\nDone" } };
    expect(summaryOf(summaries, { id: "a", updatedAt: 10 })).toBe("Wanted X\nDone");
    expect(summaryOf(summaries, { id: "a", updatedAt: 11 })).toBeNull();
    expect(summaryOf(summaries, { id: "constructor", updatedAt: 10 })).toBeNull();
  });

  it("keeps one per thread, bounded, the oldest dropped first", () => {
    let summaries = withSummary({}, "a", 10, "one");
    summaries = withSummary(summaries, "a", 20, "two");
    expect(summaries).toEqual({ a: { at: 20, text: "two" } });
    for (let at = 0; at < SUMMARIES_MAX + 10; at++) summaries = withSummary(summaries, `t${at}`, 1000 + at, "x");
    expect(Object.keys(summaries)).toHaveLength(SUMMARIES_MAX);
    expect(summaries.a).toBeUndefined();
    expect(summaries[`t${SUMMARIES_MAX + 9}`]).toBeDefined();
  });

  it("drops what is not a summary", () => {
    expect(cleanSummaries({ a: { at: 1, text: "x" }, b: { at: "1", text: "x" }, c: { at: 1, text: " " }, d: null, e: 3 })).toEqual({ a: { at: 1, text: "x" } });
    expect(cleanSummaries([1])).toEqual({});
  });

  it("finds threads by their summary in Search threads", () => {
    const threads = [thread("a", 10), thread("b", 20), thread("c", 30)];
    const summaries = { a: { at: 10, text: "Wanted the retry test fixed\nFixed" }, b: { at: 19, text: "Retry, but stale" }, c: { at: 30, text: "Compress an image" } };
    expect(summaryMatches(summaries, threads, "RETRY").map((t) => t.id)).toEqual(["a"]);
    expect(summaryMatches(summaries, threads, "  ")).toEqual([]);
  });
});

describe("what the model is asked and how its answer is read", () => {
  it("reads two clean lines, each cut", () => {
    expect(parseSummary("- Wanted the flaky retry test fixed\n- \"Fixed: the last attempt re-raises\"\n- More")).toBe("Wanted the flaky retry test fixed\nFixed: the last attempt re-raises");
    expect(parseSummary("```\n## Summary\n\nOne line only\n```")).toBe("Summary\nOne line only");
    expect(parseSummary("x".repeat(400))?.length).toBe(LINE_MAX);
    expect(parseSummary("  \n")).toBeNull();
  });

  it("gives the first request and the latest messages, bounded, without the project context", () => {
    const turns = [turn("<project_context>\nsecret\n</project_context>", "ok"), turn("Fix the retry test", "Done."), ...Array.from({ length: 20 }, (_, at) => turn(`step ${at} ${"y".repeat(800)}`, `answer ${at}`))];
    const said = transcript(turns);
    expect(said.startsWith("Assistant: ok\n…\n")).toBe(true);
    expect(said).not.toContain("secret");
    expect(said).toContain("answer 19");
    expect(said).not.toContain("answer 0\n");
    expect(said.length).toBeLessThan(7_000);
    expect(transcript([turn("Fix it", "Fixed")])).toBe("User: Fix it\nAssistant: Fixed");
    expect(transcript([])).toBe("");
    expect(summaryPrompt("Fix it", "User: Fix it")).toBe(`${SUMMARY_PROMPT}\n\nThe conversation "Fix it":\nUser: Fix it`);
  });

  it("reads the thread, then asks the served model in a read-only thread that is not kept", async () => {
    const seen: string[] = [];
    const server = new ScriptedServer({
      "thread/read": () => ({ thread: { ...thread("w9", 5), turns: [turn("Fix the retry test", "Fixed: the last attempt re-raises.")] } }),
      "thread/start": () => ({ thread: { id: "e1" } }),
      "turn/start": (params, self) => {
        seen.push((params.input as { text: string }[])[0].text);
        self.notify("item/completed", { threadId: "e1", turnId: "x", item: { type: "agentMessage", id: "a", text: "Wanted the retry test fixed\nFixed, the last attempt re-raises" } });
        self.notify("turn/completed", { threadId: "e1", turn: { id: "x", status: "completed", items: [], error: null } });
        return { turn: { id: "x" } };
      },
      "thread/unsubscribe": () => ({}),
    });
    const found = await summarize(server.client, server.listen, thread("w9", 5), "qwen");
    expect(found).toBe("Wanted the retry test fixed\nFixed, the last attempt re-raises");
    expect(server.sent[0]).toEqual({ method: "thread/read", params: { threadId: "w9", includeTurns: true } });
    expect(server.sent[1]).toEqual({ method: "thread/start", params: { cwd: "/w/fetcher", model: "qwen", ephemeral: true, sandbox: "read-only", approvalPolicy: "never" } });
    expect(server.methods()).not.toContain("thread/resume");
    expect(seen[0]).toBe(summaryPrompt("Thread w9", "User: Fix the retry test\nAssistant: Fixed: the last attempt re-raises."));
  });

  it("asks nothing for a thread with nothing said", async () => {
    const server = new ScriptedServer({ "thread/read": () => ({ thread: thread("w9", 5) }) });
    expect(await summarize(server.client, server.listen, thread("w9", 5), "qwen")).toBeNull();
    expect(server.methods()).toEqual(["thread/read"]);
  });
});
