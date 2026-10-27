// Night Shift's results in the inbox and its archive (DESKTOP §16 B41).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { isUnread, nightUnread, searchArchive, unreadRuns } from "./nightinbox";

const task = (night: string, id: string, fields: Record<string, unknown>) => {
  fs.mkdirSync(path.join(night, "tasks"), { recursive: true });
  fs.writeFileSync(path.join(night, "tasks", `${id}.json`), JSON.stringify({ id, repo: "/w/app", task: `Task ${id}`, status: "done", branch: `night/${id}`, history: [], ...fields }));
};

describe("Night Shift's unread results", () => {
  it("counts a finished run unread until it is opened or everything is marked read", () => {
    const done = { id: "a", status: "done", at: "2026-10-20T03:00:00+01:00" };
    expect(isUnread(done, null, [])).toBe(true);
    expect(isUnread(done, null, ["a"])).toBe(false);
    expect(isUnread(done, "2026-10-21T08:00:00Z", [])).toBe(false);
    expect(isUnread(done, "2026-10-19T08:00:00Z", [])).toBe(true);
    expect(isUnread({ ...done, status: "queued" }, null, [])).toBe(false);
    expect(isUnread({ ...done, status: "no-change" }, null, [])).toBe(true);
    expect(unreadRuns([done, { ...done, id: "b" }, { ...done, id: "c", status: "running" }], null, ["b"])).toBe(1);
    // Preferences of the wrong shape count as no marks.
    expect(unreadRuns([done], 5, "a")).toBe(1);
  });

  it("counts from the files and the window's preferences, for the tray", () => {
    const night = fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-inbox-"));
    task(night, "20261020-0100-aaa", { history: [{ at: "2026-10-20T03:00:00+01:00", status: "done" }] });
    task(night, "20261020-0200-bbb", { status: "failed", history: [{ at: "2026-10-20T04:00:00+01:00", status: "failed" }] });
    task(night, "20261021-2200-ccc", { status: "queued" });
    expect(nightUnread(night, { nightSeen: null, nightRead: [] })).toBe(2);
    expect(nightUnread(night, { nightSeen: null, nightRead: ["20261020-0100-aaa"] })).toBe(1);
    expect(nightUnread(path.join(night, "missing"), {})).toBe(0);
  });
});

describe("Night Shift's archive", () => {
  it("finds archived runs and reports by every word, newest first, and lists the newest with no words", () => {
    const night = fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-archive-"));
    const archive = path.join(night, "archive");
    task(archive, "20260901-0100-aaa", { task: "Make the retry test table-driven", history: [{ at: "2026-09-01T03:00:00+01:00", status: "done" }] });
    task(archive, "20260902-0100-bbb", { task: "Add type hints", status: "stalled", history: [{ at: "2026-09-02T03:00:00+01:00", status: "stalled" }] });
    task(night, "20261020-0100-ccc", { task: "Retry the fetch" });
    fs.mkdirSync(path.join(archive, "reports"));
    fs.writeFileSync(path.join(archive, "reports", "2026-09-01.md"), "# Night Shift\n\n### 20260901-0100-aaa — done\n\n- Task: Make the retry test table-driven\n- Tests: passed\n");
    fs.writeFileSync(path.join(archive, "reports", "2026-09-02.md"), "# Night Shift\n\n- Type hints stalled\n");

    const found = searchArchive(night, "RETRY table");
    expect(found.tasks.map((entry) => entry.id)).toEqual(["20260901-0100-aaa"]);
    expect(found.reports).toEqual([{ name: "2026-09-01.md", lines: ["- Task: Make the retry test table-driven"] }]);
    expect(searchArchive(night, "stalled").tasks.map((entry) => entry.id)).toEqual(["20260902-0100-bbb"]);
    const all = searchArchive(night, "");
    expect(all.tasks.map((entry) => entry.id)).toEqual(["20260902-0100-bbb", "20260901-0100-aaa"]);
    expect(all.reports.map((entry) => entry.name).sort()).toEqual(["2026-09-01.md", "2026-09-02.md"]);
    expect(searchArchive(path.join(night, "none"), "x")).toEqual({ tasks: [], reports: [] });
    expect(() => searchArchive(night, 5)).toThrow("search the archive");
    expect(() => searchArchive(night, "x".repeat(201))).toThrow("search the archive");
  });
});
