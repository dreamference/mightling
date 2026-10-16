// Threads in the sidebar (DESKTOP_PARITY §2.1): pins first, Recent, the order Ctrl+Tab walks, the
// next thread that needs the user, sections, links; and find in thread's matcher.
import { describe, expect, it } from "vitest";

import { occurrences } from "./find";
import type { Thread } from "./protocol/v2/Thread";
import { moveToSection, nextAttention, pinnedFirst, recentThreads, stepThread, threadLink, toggled } from "./threads";

const thread = (id: string, updatedAt: number) => ({ id, updatedAt, recencyAt: null } as unknown as Thread);

describe("threads in the sidebar", () => {
  it("put pinned ones first in pin order, and list the most recent", () => {
    expect(pinnedFirst(["a", "b", "c", "d"], ["c", "a"], (id) => id)).toEqual(["c", "a", "b", "d"]);
    expect(toggled(["a"], "b")).toEqual(["b", "a"]);
    expect(toggled(["a", "b"], "a")).toEqual(["b"]);
    expect(recentThreads([thread("old", 1), thread("new", 9), thread("mid", 5)], 2).map((entry) => entry.id)).toEqual(["new", "mid"]);
  });

  it("walk with Ctrl+Tab and find the next thread that needs the user", () => {
    const order = ["a", "b", "c", "d"];
    expect(stepThread(order, "b", 1)).toBe("c");
    expect(stepThread(order, "a", -1)).toBe("d");
    expect(stepThread(order, null, 1)).toBe("a");
    expect(stepThread([], "a", 1)).toBeNull();
    expect(nextAttention(order, "b", new Set(["a"]), new Set(["c"]))).toBe("a");
    expect(nextAttention(order, "b", new Set(), new Set(["a", "d"]))).toBe("d");
    expect(nextAttention(order, "b", new Set(["b"]), new Set())).toBeNull();
  });

  it("move a thread between sections, and link to it", () => {
    const sections = [{ id: "s1", name: "Bugs", threads: ["a"] }, { id: "s2", name: "Ideas", threads: [] }];
    expect(moveToSection(sections, "a", "s2")).toEqual([{ id: "s1", name: "Bugs", threads: [] }, { id: "s2", name: "Ideas", threads: ["a"] }]);
    expect(moveToSection(sections, "a", null)[0].threads).toEqual([]);
    expect(threadLink("019a-x")).toBe("ling://threads/019a-x");
  });

  it("find every match, whatever its case", () => {
    expect(occurrences("Retry, retry, RETRY", "retry")).toEqual([0, 7, 14]);
    expect(occurrences("abc", "")).toEqual([]);
  });
});
