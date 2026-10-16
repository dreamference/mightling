// Worktree threads in the page (DESKTOP_PARITY §2.6): the folder a turn runs in after a hand-off,
// the project a worktree thread is listed under, the cleanup's choice, and the [desktop] settings.
// The git half runs against scratch repositories in desktop/electron/src/worktree.test.ts.
import { describe, expect, it } from "vitest";

import type { WorktreeLink } from "./prefs";
import type { Thread } from "./protocol/v2/Thread";
import { initialState, projects, reduce } from "./store";
import { DEFAULT_KEEP, carry, cleanupVictims, projectFolder, turnCwd, worktreeSettings } from "./worktrees";

const link: WorktreeLink = {
  checkout: "/home/u/.mightling/worktrees/ab12/app", cwd: "/home/u/.mightling/worktrees/ab12/app/src",
  source: "/home/u/src/app", sourceCwd: "/home/u/src/app/src", location: "worktree", snapshot: null,
};

describe("worktree threads", () => {
  it("run their turns in the worktree, or in the same folder of the local checkout after a hand-off", () => {
    expect(turnCwd(link)).toBe("/home/u/.mightling/worktrees/ab12/app/src");
    expect(turnCwd({ ...link, location: "local" })).toBe("/home/u/src/app/src");
    expect(turnCwd(undefined)).toBeNull();
    expect(carry("/a/b", "/a/b", "/c")).toBe("/c");
    expect(carry("/a/bc/d", "/a/b", "/c")).toBe("/a/bc/d");
  });

  it("are listed under the project they were started for", () => {
    const thread = (id: string, cwd: string) => ({ id, cwd, updatedAt: 1, recencyAt: null, ephemeral: false, parentThreadId: null, turns: [] } as unknown as Thread);
    const state = reduce(initialState, { type: "threads", list: [thread("w", link.cwd), thread("l", link.sourceCwd)] });
    const links = { w: link };
    expect(projectFolder({ id: "w", cwd: link.cwd }, links)).toBe(link.sourceCwd);
    expect(projects(state, null, (entry) => projectFolder(entry, links)).map((project) => [project.cwd, project.threads.map((entry) => entry.id).sort()])).toEqual([
      [link.sourceCwd, ["l", "w"]],
    ]);
  });

  it("are cleaned up oldest first beyond the limit, never a running, pinned, permanent or new one", () => {
    const entries = [
      { root: "/w/1", owner: "t1", modified: 1 },
      { root: "/w/2", owner: "t2", modified: 2 },
      { root: "/w/3", owner: "t3", modified: 3 },
      { root: "/w/4", owner: null, modified: 4 },
      { root: "/w/5", owner: "t5", modified: 5 },
    ];
    expect(cleanupVictims(entries, 3, new Set(), [], null)).toEqual(["/w/1", "/w/2"]);
    expect(cleanupVictims(entries, 3, new Set(["t1"]), ["/w/2"], null)).toEqual(["/w/3", "/w/4"]);
    expect(cleanupVictims(entries, 5, new Set(), [], null)).toEqual([]);
    expect(cleanupVictims(entries, 4, new Set(), [], "/w/1")).toEqual(["/w/2"]);
  });

  it("read the [desktop] settings the codex-worktree crate reads, with its defaults", () => {
    expect(worktreeSettings(null)).toEqual({ root: null, autoCleanup: true, keep: DEFAULT_KEEP });
    expect(DEFAULT_KEEP).toBe(15);
    expect(worktreeSettings({ "git-worktree-root": " /data/wt ", "worktree-auto-cleanup-enabled": false, "worktree-keep-count": 4 })).toEqual({ root: "/data/wt", autoCleanup: false, keep: 4 });
    expect(worktreeSettings({ "worktree-keep-count": 0 }).keep).toBe(DEFAULT_KEEP);
  });
});
