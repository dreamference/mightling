// Night Shift's results in Scheduled's inbox (DESKTOP §16 B41).
import { describe, expect, it } from "vitest";

import { isUnread, markRead, NIGHT_READ_MAX, prunedNight, queuedAt, unreadRuns } from "./nightinbox";

const run = (id: string, status = "done", at = "2026-10-20T03:00:00+01:00") => ({ id, status, at });

describe("Night Shift's inbox", () => {
  it("keeps a finished run unread until it is opened or everything is marked read", () => {
    expect(isUnread(run("a"), null, [])).toBe(true);
    expect(isUnread(run("a"), null, ["a"])).toBe(false);
    expect(isUnread(run("a"), "2026-10-21T00:00:00Z", [])).toBe(false);
    expect(isUnread(run("a", "queued"), null, [])).toBe(false);
    expect(isUnread(run("a", "no-change"), null, [])).toBe(true);
    expect(unreadRuns([run("a"), run("b"), run("c", "running")], null, ["a"])).toBe(1);
  });

  it("compares a run's finish with Mark all as read as times, not as text in two time zones", () => {
    // Night Shift writes local time with its offset; Mark all as read stores UTC (`toISOString`).
    expect(isUnread(run("a", "done", "2026-10-20T10:50:00-05:00"), "2026-10-20T15:45:00.000Z", [])).toBe(true);
    expect(isUnread(run("a", "done", "2026-10-20T16:40:00+01:00"), "2026-10-20T15:45:00.000Z", [])).toBe(false);
  });

  it("remembers an opened run once, the oldest dropped beyond the bound", () => {
    expect(markRead(["a"], "b")).toEqual(["a", "b"]);
    expect(markRead(["a", "b"], "a")).toEqual(["a", "b"]);
    const many = Array.from({ length: NIGHT_READ_MAX }, (_, index) => `t${index}`);
    const marked = markRead(many, "new");
    expect(marked).toHaveLength(NIGHT_READ_MAX);
    expect(marked[0]).toBe("t1");
    expect(marked.at(-1)).toBe("new");
  });

  it("reads when a task was queued from its id", () => {
    expect(queuedAt("20261017-0930-abc")).toBe(new Date(2026, 9, 17, 9, 30).getTime());
    expect(queuedAt("20261017-0930")).toBe(new Date(2026, 9, 17, 9, 30).getTime());
    expect(queuedAt("custom")).toBeNull();
  });

  it("forgets the marks and thread ties of tasks gone from the queue, never one just queued", () => {
    const now = new Date(2026, 9, 27, 12, 0).getTime();
    const prefs = {
      nightRead: ["20260901-0100-aaa", "20261020-0100-bbb", "20261027-1130-ccc"],
      nightThreads: { "20260901-0100-aaa": "t1", "20261020-0100-bbb": "t2", "20261027-1130-ccc": "t3" },
    };
    // Archived: aaa is gone; ccc was queued half an hour ago and is not listed yet, so it stays.
    expect(prunedNight(prefs, ["20261020-0100-bbb"], now)).toEqual({
      nightRead: ["20261020-0100-bbb", "20261027-1130-ccc"],
      nightThreads: { "20261020-0100-bbb": "t2", "20261027-1130-ccc": "t3" },
    });
    expect(prunedNight(prefs, ["20260901-0100-aaa", "20261020-0100-bbb"], now)).toBeNull();
    expect(prunedNight({ nightRead: [], nightThreads: { "20260901-0100-aaa": "t1" } }, [], now)).toEqual({ nightThreads: {} });
  });
});
