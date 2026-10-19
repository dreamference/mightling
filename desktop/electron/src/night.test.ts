// The Scheduled view's reading of Night Shift's files (DESKTOP_PARITY §2.12), in a scratch folder,
// and its queueing through a stand-in `ling night`.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { addTask, dropTask, readReport, readReports, readTasks } from "./night";

describe("Night Shift for the Scheduled view", () => {
  it("reads the tasks newest first and the reports, and refuses a report name that leaves the folder", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-"));
    fs.mkdirSync(path.join(dir, "tasks"));
    fs.mkdirSync(path.join(dir, "reports"));
    const task = (id: string, status: string, at: string) => ({ id, repo: "/w/app", task: `task ${id}`, status, branch: `night/${id}`, test: null, on: null, history: [{ at: "2026-10-16T10:00:00+01:00", status: "queued" }, { at, status }], result: null });
    fs.writeFileSync(path.join(dir, "tasks", "a.json"), JSON.stringify(task("a", "done", "2026-10-17T03:00:00+01:00")));
    fs.writeFileSync(path.join(dir, "tasks", "b.json"), JSON.stringify(task("b", "queued", "2026-10-16T21:00:00+01:00")));
    fs.writeFileSync(path.join(dir, "tasks", "c.json"), "{ half written");
    fs.writeFileSync(path.join(dir, "reports", "2026-10-17.md"), "# Night Shift\n\n## /w/app\n- a: done\n");
    expect(readTasks(dir).map((entry) => [entry.id, entry.status])).toEqual([["a", "done"], ["b", "queued"]]);
    expect(readReports(dir).map((entry) => entry.name)).toEqual(["2026-10-17.md"]);
    expect(readReport(dir, "2026-10-17.md")).toContain("a: done");
    expect(() => readReport(dir, "../tasks/a.json")).toThrow("not a report");
    expect(readTasks(path.join(dir, "none"))).toEqual([]);
  });

  it("queues and drops through ling night, never writing a task itself", async () => {
    // Real path: on macOS the temporary folder is a link (/var → /private/var), and ling reports where it runs.
    const home = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-cli-")));
    const log = path.join(home, "calls.txt");
    const ling = path.join(home, "ling");
    fs.writeFileSync(ling, `#!/bin/sh\necho "$PWD|$*" >> ${log}\necho "Queued 20261017-0900 on branch night/20261017-0900."\n`);
    fs.chmodSync(ling, 0o755);
    await expect(addTask(ling, home, "Update the changelog", "npm test")).resolves.toContain("Queued");
    await dropTask(ling, home, "20261017-0900");
    expect(fs.readFileSync(log, "utf8").trim().split("\n")).toEqual([`${home}|night add --test npm test Update the changelog`, `${home}|night drop 20261017-0900`]);
    await expect(addTask(ling, "relative", "x", null)).rejects.toThrow("project folder");
    await expect(dropTask(ling, home, "../x")).rejects.toThrow("not a task");
  });
});
