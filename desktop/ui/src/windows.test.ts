// Several windows (DESKTOP_ELECTRON §13): a pane's address, a thread's composer, and which one window answers the server.
import { describe, expect, it } from "vitest";

import { answersFor, composerOf, paneKey, paneOf } from "./windows";

describe("a window's address", () => {
  it("names a detached pane, and nothing else", () => {
    expect(paneOf("#pane=terminal&thread=t1")).toEqual({ kind: "terminal", thread: "t1" });
    expect(paneOf("#pane=review&thread=a%2Fb")).toEqual({ kind: "review", thread: "a/b" });
    expect(paneOf("#pane=preview&thread=t1")).toBeNull();
    expect(paneOf("#pane=terminal")).toBeNull();
    expect(paneOf("#work")).toBeNull();
    expect(paneOf("#popout=t1")).toBeNull();
    expect(paneKey("review", "t1")).toBe("review:t1");
  });
});

describe("a thread's composer", () => {
  const state = { self: "2", owners: { t1: "1", t2: "2" }, fallback: "1" };

  it("is here when this window holds it, nobody does, or the page is not in the app; elsewhere otherwise", () => {
    expect(composerOf(state, "t1")).toBe("elsewhere");
    expect(composerOf(state, "t2")).toBe("here");
    expect(composerOf(state, "t3")).toBe("here");
    expect(composerOf(state, null)).toBe("here");
    expect(composerOf(null, "t1")).toBe("here");
  });

  it("decides the one window that answers the server's unasked requests", () => {
    expect(answersFor(state, "t1", false)).toBe(false);
    expect(answersFor(state, "t2", false)).toBe(true);
    // Nobody shows t3: the most recent window answers, here window 1.
    expect(answersFor(state, "t3", false)).toBe(false);
    expect(answersFor({ ...state, self: "1" }, "t3", false)).toBe(true);
    expect(answersFor({ ...state, self: "1" }, null, false)).toBe(true);
    expect(answersFor({ ...state, fallback: null }, "t3", false)).toBe(true);
    // A pane never answers; a browser page always does.
    expect(answersFor({ ...state, self: "1" }, "t1", true)).toBe(false);
    expect(answersFor(null, "t1", false)).toBe(true);
  });
});
