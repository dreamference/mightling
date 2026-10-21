// Threads in the sidebar (DESKTOP_PARITY §2.1): pins first, Recent, the order Ctrl+Tab walks, the
// next thread that needs the user, sections, links; and find in thread's matcher.
import { describe, expect, it } from "vitest";

import { occurrences } from "./find";
import type { Thread } from "./protocol/v2/Thread";
import { NAME_MAX, moveToSection, nextAttention, pinnedFirst, recentThreads, renameStart, stepThread, threadLink, toggled } from "./threads";

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

describe("the rename box", () => {
  it("starts with the title the sidebar shows, never a placeholder", () => {
    expect(renameStart({ name: "Hello script", preview: "Create hello.sh" })).toBe("Hello script");
    expect(renameStart({ name: null, preview: "Create hello.sh\nand run it" })).toBe("Create hello.sh and run it");
    expect(renameStart({ name: null, preview: "" })).toBe("");
    expect(renameStart({ name: null, preview: "x".repeat(500) })).toHaveLength(NAME_MAX);
  });
});

describe("Scheduled's inbox", () => {
  it("counts the finished tasks newer than the last look", async () => {
    const { unseen } = await import("./ScheduledView");
    const task = (status: string, at: string) => ({ id: at, repo: "/w", task: "t", status, branch: null, test: null, on: null, at, result: null });
    const tasks = [task("done", "2026-10-17T03:00:00"), task("failed", "2026-10-16T03:00:00"), task("queued", "2026-10-17T09:00:00")];
    expect(unseen(tasks, null)).toBe(2);
    expect(unseen(tasks, "2026-10-16T12:00:00")).toBe(1);
  });
});

describe("subagents and memories", () => {
  it("list a thread's subagents, tell which are working, and count every working thread", async () => {
    const { initialState: start, reduce: step, subagents, isWorking, workingCount } = await import("./store");
    const sub = (id: string, parent: string | null, createdAt: number, status = "idle") => ({ id, parentThreadId: parent, createdAt, updatedAt: createdAt, recencyAt: null, ephemeral: false, cwd: "/w", turns: [], status: { type: status, activeFlags: [] } } as unknown as Thread);
    let state = step(start, { type: "threads", list: [sub("p", null, 1), sub("a", "p", 2, "active"), sub("b", "p", 3), sub("x", null, 4)] });
    expect(subagents(state, "p").map((thread) => thread.id)).toEqual(["b", "a"]);
    expect(isWorking(state, "a")).toBe(true);
    state = step(state, { type: "notification", notification: { method: "thread/status/changed", params: { threadId: "a", status: { type: "idle" } } } as never });
    expect(isWorking(state, "a")).toBe(false);
    state = step(state, { type: "notification", notification: { method: "turn/started", params: { threadId: "b", turn: { id: "t", status: "inProgress", items: [], error: null } } } as never });
    expect(workingCount(state)).toBe(1);
    const { memorySwitches } = await import("./SettingsDialog");
    expect(memorySwitches({})).toEqual({ enabled: false, use: true, generate: true, external: true });
    expect(memorySwitches({ features: { memories: true }, memories: { use_memories: false, disable_on_external_context: true } })).toEqual({ enabled: true, use: false, generate: true, external: false });
  });
});

describe("Settings › Your documents", () => {
  it("says what each collection holds, what failed and when it was indexed", async () => {
    const { collectionSummary, ago, humanBytes } = await import("./SettingsDialog");
    const counts = { documents: 12, indexed: 10, pending: 1, failed: 1, duplicates: 0, skipped: 0, chunks: 80, embedded: 70, needs_ocr_pages: 3, needs_ocr_documents: 1 };
    const now = Date.now() / 1000;
    const summary = collectionSummary({ name: "documents", root: "/home/x/Documents", enabled: true, default: true, counts, last_status: "ok", last_run: now - 600, disk_bytes: 3 << 20 });
    expect(summary).toBe("10 documents · 80 passages (10 not yet searchable by meaning) · 1 waiting · 1 could not be read · 3 scanned pages without text · last indexed 10 min ago · 3.0 MB");
    expect(ago(null)).toBe("never");
    expect(ago(now - 3 * 86400, now)).toBe("3 days ago");
    expect(humanBytes(2048)).toBe("2 KB");
  });
});
