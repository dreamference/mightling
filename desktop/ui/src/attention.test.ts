// Activity's Needs you tab (DESKTOP_PARITY §4.5 item 17): waiting first, then working, then unread;
// subagents and throwaway threads left out; the Work, Ask and Pinned filters.
import { describe, expect, it } from "vitest";

import { attentionList, filtered } from "./attention";
import type { Thread } from "./protocol/v2/Thread";

const thread = (id: string, updatedAt: number, extra: Partial<Thread> = {}) => ({ id, updatedAt, recencyAt: null, ephemeral: false, parentThreadId: null, cwd: "/w", ...extra }) as unknown as Thread;

describe("what needs you", () => {
  const threads = [thread("a", 10), thread("b", 30), thread("c", 20), thread("d", 40), thread("sub", 50, { parentThreadId: "a" }), thread("tmp", 60, { ephemeral: true }), thread("q", 25)];
  const facts = {
    waiting: new Set(["c", "sub"]), running: new Set(["b", "tmp"]), unread: new Set(["a", "b", "q"]), pinned: new Set(["a"]),
    isAsk: (entry: Thread) => entry.id === "q",
  };

  it("lists waiting threads first, then working ones, then unread, newest first in each, and leaves out subagents and throwaway threads", () => {
    const list = attentionList(threads, facts);
    expect(list.map((entry) => [entry.thread.id, entry.states])).toEqual([["c", ["waiting"]], ["b", ["running", "unread"]], ["q", ["unread"]], ["a", ["unread"]]]);
  });

  it("filters by Work, Ask and Pinned, any of the chosen ones, none chosen keeping all", () => {
    const list = attentionList(threads, facts);
    expect(filtered(list, new Set()).length).toBe(4);
    expect(filtered(list, new Set(["ask"])).map((entry) => entry.thread.id)).toEqual(["q"]);
    expect(filtered(list, new Set(["work"])).map((entry) => entry.thread.id)).toEqual(["c", "b", "a"]);
    expect(filtered(list, new Set(["pinned", "ask"])).map((entry) => entry.thread.id)).toEqual(["q", "a"]);
  });
});
