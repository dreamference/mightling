import { describe, expect, it } from "vitest";

import type { Prefs } from "./prefs";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { Thread } from "./protocol/v2/Thread";
import { clicked, deleteQuestion, forgetThreads, NAMES_LISTED, NO_CHOICE, present, rowRange } from "./selection";
import { initialState, reduce } from "./store";

const order = ["a", "b", "c", "d", "e"];
const plain = { ctrlKey: false, metaKey: false, shiftKey: false };
const ctrl = { ...plain, ctrlKey: true };
const cmd = { ...plain, metaKey: true };
const shift = { ...plain, shiftKey: true };

describe("choosing threads in the sidebar (DESKTOP §16 B36)", () => {
  it("leaves a plain click to open the thread", () => {
    expect(clicked(NO_CHOICE, order, 1, plain, null)).toBeNull();
    expect(clicked({ ids: ["a"], anchor: "a" }, order, 1, plain, null)).toBeNull();
    expect(clicked(NO_CHOICE, order, 9, ctrl, null)).toBeNull();
  });

  it("toggles a row with Ctrl or Cmd, which becomes the anchor", () => {
    const one = clicked(NO_CHOICE, order, 1, ctrl, null)!;
    expect(one).toEqual({ ids: ["b"], anchor: "b" });
    const two = clicked(one, order, 3, cmd, null)!;
    expect(two).toEqual({ ids: ["b", "d"], anchor: "d" });
    expect(clicked(two, order, 1, ctrl, null)).toEqual({ ids: ["d"], anchor: "b" });
  });

  it("chooses a range with Shift from the anchor, else from the thread shown, else the row alone", () => {
    expect(clicked({ ids: ["b"], anchor: "b" }, order, 3, shift, null)).toEqual({ ids: ["b", "c", "d"], anchor: "b" });
    // Upwards, and a second Shift-click counts from the same anchor, replacing the range.
    expect(clicked({ ids: ["b", "c", "d"], anchor: "d" }, order, 0, shift, null)).toEqual({ ids: ["a", "b", "c", "d"], anchor: "d" });
    expect(clicked(NO_CHOICE, order, 4, shift, "c")).toEqual({ ids: ["c", "d", "e"], anchor: "c" });
    expect(clicked(NO_CHOICE, order, 2, shift, null)).toEqual({ ids: ["c"], anchor: "c" });
    // With Ctrl too, the range joins what was chosen.
    expect(clicked({ ids: ["a"], anchor: "c" }, order, 4, { ...shift, ctrlKey: true }, null)).toEqual({ ids: ["a", "c", "d", "e"], anchor: "c" });
  });

  it("counts a thread listed twice where it is nearest the click, and lists each thread once", () => {
    // `b` pinned at the top and again in its project.
    const rows = ["b", "x", "a", "b", "c", "d"];
    expect(rowRange(rows, "b", 5)).toEqual(["b", "c", "d"]);
    expect(rowRange(rows, "b", 1)).toEqual(["b", "x"]);
    expect(rowRange(["a", "b", "a"], "a", 1)).toEqual(["a", "b"]);
    // An anchor no longer shown: the row alone.
    expect(rowRange(rows, "gone", 4)).toEqual(["c"]);
  });

  it("drops threads that left the window", () => {
    const choice = { ids: ["a", "b", "c"], anchor: "b" };
    expect(present(choice, () => true)).toBe(choice);
    expect(present(choice, (id) => id !== "b")).toEqual({ ids: ["a", "c"], anchor: null });
  });
});

describe("the delete confirmation", () => {
  it("names the thread, says it cannot be undone and what goes with it", () => {
    const text = deleteQuestion(["Fix the flaky retry test"], false);
    expect(text).toMatch(/^Delete this thread for good\? It cannot be restored/);
    expect(text).toContain("Subagents' threads started from it go too.");
    expect(text).toContain("• Fix the flaky retry test");
    expect(text).not.toContain("question's folder");
  });

  it("counts several, names each, and says a question's files stay", () => {
    const text = deleteQuestion(["One", "Two", "Three"], true);
    expect(text).toMatch(/^Delete these 3 threads for good\? They cannot be restored/);
    expect(text).toContain("• One\n• Two\n• Three");
    expect(text).toContain("Files the agent wrote in a question's folder stay on disk.");
  });

  it("lists a dozen names and counts the rest, each name cut to a line", () => {
    const labels = Array.from({ length: 15 }, (_, index) => `Thread ${index + 1}`);
    const text = deleteQuestion(labels, false);
    expect(text).toContain("these 15 threads");
    expect(text.match(/^• /gm)).toHaveLength(NAMES_LISTED);
    expect(text).toContain("…and 3 more");
    expect(deleteQuestion(["x".repeat(200)], false)).toContain(`• ${"x".repeat(79)}…`);
  });
});

describe("forgetting deleted threads", () => {
  const prefs = {
    pinnedThreads: ["a", "keep"],
    unreadThreads: ["b"],
    sections: [{ id: "s1", name: "Later", threads: ["a", "keep"] }, { id: "s2", name: "Other", threads: ["keep"] }],
    messageVersions: { "keep:2": ["keep", "a", "f1"], "keep:4": ["keep", "b"], "a:1": ["a", "f2"], "other:0": ["other", "f3"] },
    nightThreads: { "task-1": "a", "task-2": "keep" },
    threadSummaries: { a: { at: 1, text: "gone" }, keep: { at: 2, text: "kept" } },
    projectContextSent: { b: "stamp" },
    worktreeThreads: { a: { sourceCwd: "/src", checkout: "/wt/a", snapshot: null } },
  } as unknown as Prefs;

  it("takes them out of every preference that names a thread", () => {
    expect(forgetThreads(prefs, ["a", "b"])).toEqual({
      pinnedThreads: ["keep"],
      unreadThreads: [],
      sections: [{ id: "s1", name: "Later", threads: ["keep"] }, { id: "s2", name: "Other", threads: ["keep"] }],
      // A version taken out of an entry; an entry left with one version, or whose first version went, dropped.
      messageVersions: { "keep:2": ["keep", "f1"], "other:0": ["other", "f3"] },
      nightThreads: { "task-2": "keep" },
      threadSummaries: { keep: { at: 2, text: "kept" } },
      projectContextSent: {},
      worktreeThreads: {},
    });
  });

  it("returns only what changes", () => {
    expect(forgetThreads(prefs, ["nothing"])).toEqual({});
    expect(Object.keys(forgetThreads(prefs, ["b"])).sort()).toEqual(["messageVersions", "projectContextSent", "unreadThreads"]);
  });
});

describe("the window's state", () => {
  it("drops a thread the server deleted, and the view if it was open", () => {
    const thread = { id: "t1", cwd: "/w", updatedAt: 1, createdAt: 1, recencyAt: null, ephemeral: false, parentThreadId: null, preview: "", name: null, turns: [], status: { type: "idle" } } as unknown as Thread;
    let state = reduce(initialState, { type: "opened", thread });
    expect(state.selected).toBe("t1");
    state = reduce(state, { type: "notification", notification: { method: "thread/deleted", params: { threadId: "t1" } } as ServerNotification });
    expect(state.threads.t1).toBeUndefined();
    expect(state.selected).toBeNull();
  });
});
