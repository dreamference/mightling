// Two threads on one checkout (DESKTOP §16 B9).
import { describe, expect, it } from "vitest";

import { busyHere, sameCheckout } from "./checkouts";
import type { WorktreeLink } from "./prefs";
import type { Thread } from "./protocol/v2/Thread";
import { initialState, type WorkState } from "./store";

const view = (id: string, cwd: string, running: boolean, extra: Partial<Thread> = {}) => ({
  thread: { id, cwd, name: null, preview: id, ephemeral: false, parentThreadId: null, status: { type: "idle" }, turns: [], ...extra } as unknown as Thread,
  turns: [], activeTurnId: running ? "u" : null, usage: null,
});

describe("threads on one checkout", () => {
  it("are the same folder or one inside the other", () => {
    expect(sameCheckout("/w/app", "/w/app/")).toBe(true);
    expect(sameCheckout("/w/app", "/w/app/src")).toBe(true);
    expect(sameCheckout("/w/app", "/w/app2")).toBe(false);
  });

  it("lists the other working threads there, not subagents, throwaway threads, idle ones or itself", () => {
    const state: WorkState = { ...initialState, threads: {
      a: view("a", "/w/app", true), b: view("b", "/w/app/src", true), c: view("c", "/w/app", false),
      d: view("d", "/w/app", true, { parentThreadId: "a" } as Partial<Thread>), e: view("e", "/w/app", true, { ephemeral: true } as Partial<Thread>),
      f: view("f", "/w/other", true),
    } };
    expect(busyHere(state, {}, "/w/app", "a")).toEqual(["b"]);
    expect(busyHere(state, {}, "/w/app", null).sort()).toEqual(["a", "b"]);
    expect(busyHere(state, {}, null, null)).toEqual([]);
    // A thread handed off to its worktree works there, not in the checkout it came from.
    const links: Record<string, WorktreeLink> = { b: { checkout: "/wt/1/app", cwd: "/wt/1/app/src", source: "/w/app", sourceCwd: "/w/app/src", location: "worktree", snapshot: null } };
    expect(busyHere(state, links, "/w/app", "a")).toEqual([]);
    expect(busyHere(state, links, "/wt/1/app", null)).toEqual(["b"]);
  });
});
