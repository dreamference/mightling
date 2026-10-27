// Worktree threads in the page (DESKTOP_PARITY §2.6): the folder a turn runs in after a hand-off,
// the project a worktree thread is listed under, the cleanup's choice, and the [desktop] settings.
// The git half runs against scratch repositories in desktop/electron/src/worktree.test.ts.
import { describe, expect, it } from "vitest";

import type { WorktreeLink } from "./prefs";
import type { Thread } from "./protocol/v2/Thread";
import { initialState, projects, reduce } from "./store";
import { ACROSS_REPOS_KEY, carry, cleanupPool, cleanupVictims, DEFAULT_KEEP, defaultCheckout, projectFolder, protectedOwners, turnCwd, worktreeSettings } from "./worktrees";

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
    // A pinned thread's worktree is kept like a running or shown one's (§9.3; it was not until 1.7.1).
    expect([...protectedOwners(["t1"], "t2", ["t3"])].sort()).toEqual(["t1", "t2", "t3"]);
    expect(protectedOwners([], null, [])).toEqual(new Set());
    expect(cleanupVictims(entries, 3, protectedOwners([], null, ["t1"]), [], null)).toEqual(["/w/2", "/w/3"]);
  });

  it("read the [desktop] settings the codex-worktree crate reads, with its defaults", () => {
    expect(worktreeSettings(null)).toEqual({ root: null, autoCleanup: true, keep: DEFAULT_KEEP, acrossRepos: false });
    expect(DEFAULT_KEEP).toBe(15);
    expect(worktreeSettings({ "git-worktree-root": " /data/wt ", "worktree-auto-cleanup-enabled": false, "worktree-keep-count": 4 })).toEqual({ root: "/data/wt", autoCleanup: false, keep: 4, acrossRepos: false });
    expect(worktreeSettings({ "worktree-keep-count": 0 }).keep).toBe(DEFAULT_KEEP);
  });

  it("start a new thread Local unless the user chose worktrees (`/worktree on`)", () => {
    expect(defaultCheckout(null)).toBe("local");
    expect(defaultCheckout({})).toBe("local");
    expect(defaultCheckout({ "mightling-worktree-mode": "local" })).toBe("local");
    expect(defaultCheckout({ "mightling-worktree-mode": "nonsense" })).toBe("local");
    expect(defaultCheckout({ "mightling-worktree-mode": "worktree" })).toBe("worktree");
  });
});

describe("the keep-count across repositories (DESKTOP §16 B25)", () => {
  it("is off unless config.toml says so, so the count stays per repository", () => {
    expect(worktreeSettings(null).acrossRepos).toBe(false);
    expect(worktreeSettings({ [ACROSS_REPOS_KEY]: true }).acrossRepos).toBe(true);
    expect(worktreeSettings({ [ACROSS_REPOS_KEY]: "yes" }).acrossRepos).toBe(false);
  });

  it("weighs every repository's worktrees together, each once, and removes the oldest of all", () => {
    const a = [{ root: "/w/a/1", owner: null, modified: 5 }, { root: "/w/a/2", owner: null, modified: 9 }];
    const b = [{ root: "/w/b/1", owner: null, modified: 1 }, { root: "/w/a/2", owner: null, modified: 9 }];
    const pool = cleanupPool([a, b]);
    expect(pool.map((entry) => entry.root)).toEqual(["/w/a/1", "/w/a/2", "/w/b/1"]);
    expect(cleanupVictims(pool, 2, new Set(), [], null)).toEqual(["/w/b/1"]);
  });
});
