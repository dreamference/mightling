// The Scheduled view's reading of Night Shift's files (DESKTOP_PARITY §2.12), in a scratch folder,
// and its queueing through a stand-in `ling night`.
import fs from "node:fs";
import os from "node:os";
import { nodeScript } from "./test-ling";
import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import { addRepeating, addTask, dropTask, listRepeating, nightStatus, nodeName, readReport, readReports, readTasks } from "./night";
import { taskDetails, transcriptOf } from "./night";
import { applyTask, reportBlock, taskNote } from "./night";

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

  // Past the limit the newest are read, by when their file last changed, whatever order the folder lists them in.
  it("reads the newest tasks when there are more than it reads", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-many-"));
    fs.mkdirSync(path.join(dir, "tasks"));
    const write = (id: string) => fs.writeFileSync(path.join(dir, "tasks", `${id}.json`), JSON.stringify({ id, status: "done", history: [] }));
    write("old");
    fs.utimesSync(path.join(dir, "tasks", "old.json"), new Date("2020-01-01"), new Date("2020-01-01"));
    for (let i = 0; i < 1000; i++) write(`t${i}`);
    const list = fs.readdirSync.bind(fs);
    const spy = vi.spyOn(fs, "readdirSync").mockImplementation(((place: fs.PathLike) => {
      const names = (list(place) as string[]).filter((name) => name !== "old.json");
      return [...names, "old.json"];
    }) as typeof fs.readdirSync);
    try {
      const ids = readTasks(dir).map((entry) => entry.id);
      expect(ids).toHaveLength(1000);
      expect(ids).not.toContain("old");
    } finally {
      spy.mockRestore();
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("queues and drops through ling night, never writing a task itself", async () => {
    // The real path, as ling reports where it runs: macOS links /var to /private/var, Windows may hand out a short (8.3) name.
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-cli-")));
    const log = path.join(home, "calls.txt");
    const ling = nodeScript(home, "ling", `require("fs").appendFileSync(${JSON.stringify(log)}, process.cwd() + "|" + process.argv.slice(2).join(" ") + "\\n");
console.log("Queued 20261017-0900 on branch night/20261017-0900.");
`);
    await expect(addTask(ling, home, "Update the changelog", "npm test")).resolves.toContain("Queued");
    await dropTask(ling, home, "20261017-0900");
    expect(fs.readFileSync(log, "utf8").trim().split("\n")).toEqual([`${home}|night add --test npm test Update the changelog`, `${home}|night drop 20261017-0900`]);
    await expect(addTask(ling, "relative", "x", null)).rejects.toThrow("project folder");
    await expect(dropTask(ling, home, "../x")).rejects.toThrow("not a task");
  });
});

describe("a finished task's details (the owner's decision 7)", () => {
  const log = [
    JSON.stringify({ type: "thread.started", thread_id: "019a-night-1" }),
    JSON.stringify({ type: "item.completed", item: { id: "1", type: "command_execution", command: "pytest -q", exit_code: 1, aggregated_output: "1 failed" } }),
    JSON.stringify({ type: "item.completed", item: { id: "2", type: "file_change", changes: [{ path: "fetcher/client.py", kind: "update" }] } }),
    JSON.stringify({ type: "item.completed", item: { id: "3", type: "agent_message", text: "Fixed the retry." } }),
    "not json",
  ].join("\n");

  it("reads a ling exec log as a transcript and finds its session", () => {
    const { text, session } = transcriptOf(log);
    expect(session).toBe("019a-night-1");
    expect(text).toBe("$ pytest -q  (exit 1)\n\nChanged fetcher/client.py\n\nMightling: Fixed the retry.");
  });

  it("are the chosen attempt's when best-of-N ran: its message, its diff, its transcript", async () => {
    const night = fs.mkdtempSync(path.join(os.tmpdir(), "night-details-"));
    fs.mkdirSync(path.join(night, "tasks"));
    fs.mkdirSync(path.join(night, "logs"));
    fs.writeFileSync(path.join(night, "tasks", "t1.json"), JSON.stringify({ id: "t1", repo: "/nowhere", base: "abc", task: "Fix it", result: { branch: "night/t1", best_of: { chosen: 2 } } }));
    fs.writeFileSync(path.join(night, "logs", "t1-a2.last.txt"), "Fixed the retry.\n");
    fs.writeFileSync(path.join(night, "logs", "t1-a2.diff"), "+b\n");
    fs.writeFileSync(path.join(night, "logs", "t1-a2.jsonl"), log);
    expect(await taskDetails(night, "t1")).toEqual({ result: "Fixed the retry.", diff: "+b\n", transcript: expect.stringContaining("Fixed the retry."), session: "019a-night-1" });
    await expect(taskDetails(night, "../x")).rejects.toThrow(/not a task/);
    fs.rmSync(night, { recursive: true, force: true });
  });

  it("queues on a node or with a number of attempts, never both, and a node only by its name (DESKTOP §16 B5)", async () => {
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-on-")));
    const log = path.join(home, "calls.txt");
    const ling = nodeScript(home, "ling", `require("fs").appendFileSync(${JSON.stringify(log)}, process.argv.slice(2).join(" ") + "\\n");
console.log("Queued.");
`);
    await addTask(ling, home, "Bump the lock file", null, "second-node", null);
    await addTask(ling, home, "Fix the flaky test", "npm test", null, 3);
    expect(fs.readFileSync(log, "utf8").trim().split("\n")).toEqual(["night add --on second-node Bump the lock file", "night add --test npm test --best-of 3 Fix the flaky test"]);
    await expect(addTask(ling, home, "x", null, "second-node", 3)).rejects.toThrow("not both");
    await expect(addTask(ling, home, "x", null, "10.0.0.7", null)).rejects.toThrow("not an address");
    await expect(addTask(ling, home, "x", null, "a node", null)).rejects.toThrow("not an address");
    await expect(addTask(ling, home, "x", null, null, 0)).rejects.toThrow("best-of");
    await expect(addTask(ling, home, "x", null, null, 2.5)).rejects.toThrow("best-of");
    expect(nodeName(" spark-2 ")).toBe("spark-2");
    expect(nodeName("-x")).toBeNull();
    expect(nodeName("fe80::1")).toBeNull();
  });

  it("says whether the timer is on and its window, from the unit file only", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-timer-"));
    expect(await nightStatus(home, "linux")).toEqual({ timer: "off", window: null });
    expect(await nightStatus(home, "darwin")).toEqual({ timer: "unsupported", window: null });
    const units = path.join(home, ".config", "systemd", "user");
    fs.mkdirSync(units, { recursive: true });
    fs.writeFileSync(path.join(units, "mightling-night.timer"), "[Unit]\nDescription=Night Shift\n# Night Shift window: 01:00-07:00\n[Timer]\nOnCalendar=*-*-* 01:00:00\n");
    expect(await nightStatus(home, "linux")).toEqual({ timer: "on", window: "01:00-07:00" });
    fs.writeFileSync(path.join(units, "mightling-night.timer"), "[Timer]\nOnCalendar=daily\n");
    expect(await nightStatus(home, "linux")).toEqual({ timer: "on", window: null });
  });
});

describe("Night Shift inside a thread (DESKTOP §16 B40)", () => {
  it("reads a task's attempts and when it was applied", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-b40-"));
    fs.mkdirSync(path.join(dir, "tasks"));
    fs.writeFileSync(path.join(dir, "tasks", "a.json"), JSON.stringify({ id: "a", repo: "/w/app", task: "t", status: "done", attempts: 3, applied: { at: "2026-10-28T09:00:00+01:00", into: "/w/app" }, history: [] }));
    fs.writeFileSync(path.join(dir, "tasks", "b.json"), JSON.stringify({ id: "b", repo: "/w/app", task: "t", status: "queued", history: [] }));
    const tasks = readTasks(dir);
    expect(tasks.find((task) => task.id === "a")).toMatchObject({ attempts: 3, applied: "2026-10-28T09:00:00+01:00" });
    expect(tasks.find((task) => task.id === "b")).toMatchObject({ attempts: null, applied: null });
  });

  it("finds a task's block in the newest report that names it, and only that block", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-note-"));
    fs.mkdirSync(path.join(dir, "reports"));
    const report = "# Night Shift — 2026-10-28\n\n## /w/app\n\n### 20261027-2200-abc — done\n\n- Task: Make the retry test deterministic\n- Branch: `night/20261027-2200-abc`, not merged\n- Attempts: 1, nudges: 0, wall time: 4 min 2 s\n- Best of 3: 3 of 3 attempts ran; chose attempt 2 by judge\n  - Attempt 1: done\n\n### 20261027-2300-def — stalled\n\n- Task: other\n\n## Review\n\n- No branch to review.\n";
    expect(reportBlock(report, "20261027-2200-abc")).toBe(report.split("\n").slice(4, 12).join("\n").trim());
    expect(reportBlock(report, "20261027-2300-def")).toBe("### 20261027-2300-def — stalled\n\n- Task: other");
    expect(reportBlock(report, "20261027-2200")).toBeNull();
    fs.writeFileSync(path.join(dir, "reports", "2026-10-27.md"), "## /w/app\n\n### 20261027-2200-abc — interrupted\n\n- cut off\n");
    fs.utimesSync(path.join(dir, "reports", "2026-10-27.md"), new Date("2026-10-27T07:00:00Z"), new Date("2026-10-27T07:00:00Z"));
    fs.writeFileSync(path.join(dir, "reports", "2026-10-28.md"), report);
    expect(taskNote(dir, "20261027-2200-abc")).toEqual({ report: "2026-10-28.md", text: expect.stringContaining("chose attempt 2") });
    expect(taskNote(dir, "nothing-here")).toBeNull();
    expect(() => taskNote(dir, "../x")).toThrow("not a task");
  });

  it("applies through ling night apply in the thread's folder, refusing a bad id or folder", async () => {
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-apply-")));
    const log = path.join(home, "calls.txt");
    const ling = nodeScript(home, "ling", `require("fs").appendFileSync(${JSON.stringify(log)}, process.cwd() + "|" + process.argv.slice(2).join(" ") + "\\n");
console.log("Applied night/20261027-2200-abc here as uncommitted changes.");
`);
    await expect(applyTask(ling, home, "20261027-2200-abc")).resolves.toContain("Applied");
    expect(fs.readFileSync(log, "utf8").trim()).toBe(`${home}|night apply 20261027-2200-abc`);
    await expect(applyTask(ling, home, "a b")).rejects.toThrow("not a task");
    await expect(applyTask(ling, "relative", "x")).rejects.toThrow("folder");
    await expect(applyTask(ling, path.join(home, "missing"), "x")).rejects.toThrow("folder");
  });
});

describe("repeating tasks on chosen weekdays, every N nights or a day of the month (DESKTOP §16 B42)", () => {
  it("passes each cadence's checked flags to ling-admin and reads its templates back", async () => {
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-night-every-")));
    const log = path.join(home, "calls.txt");
    const admin = nodeScript(home, "ling-admin", `require("fs").appendFileSync(${JSON.stringify(log)}, process.cwd() + "|" + process.argv.slice(2).join(" ") + "\\n");
if (process.argv[4] === "list") {
  console.log(JSON.stringify({ id: "r000000000001", repo: "/w/app", task: "Audit", test: null, cadence: "days", weekday: null, days: ["mon", "wed"], every: null, day: null, last_queued: null, last_error: null }));
  console.log(JSON.stringify({ id: "r000000000002", repo: "/w/app", task: "Backup", test: "pytest", cadence: "every", weekday: null, days: null, every: 3, day: null, last_queued: "2026-10-27", last_error: null }));
  console.log(JSON.stringify({ id: "r000000000003", repo: "/w/app", task: "Clean", test: null, cadence: "monthly", weekday: null, days: null, every: null, day: 31, last_queued: null, last_error: null }));
  console.log(JSON.stringify({ id: "r000000000004", repo: "/w/app", task: "Later", test: null, cadence: "hourly", last_queued: null, last_error: null }));
} else {
  console.log("OK");
}
`);
    await addRepeating(admin, home, "Audit", null, "days", { days: ["mon", "wed"] });
    await addRepeating(admin, home, "Backup", "pytest", "every", { every: 3 });
    await addRepeating(admin, home, "Clean", null, "monthly", { day: 31 });
    for (const [cadence, options] of [["every", { every: 0 }], ["every", { every: "3; rm" }], ["monthly", { day: 32 }], ["days", { days: "mon,--test" }], ["days", "mon"], ["hourly", {}]] as const) {
      await expect(addRepeating(admin, home, "Bad", null, cadence, options)).rejects.toThrow();
    }
    expect(fs.readFileSync(log, "utf8").trim().split("\n")).toEqual([
      `${home}|night every add --days mon,wed -- Audit`,
      `${home}|night every add --every 3 --test pytest -- Backup`,
      `${home}|night every add --monthly 31 -- Clean`,
    ]);

    const tasks = await listRepeating(admin);
    expect(tasks.map((entry) => entry.id)).toEqual(["r000000000001", "r000000000002", "r000000000003"]);
    expect(tasks[0]).toMatchObject({ cadence: "days", days: ["mon", "wed"], every: null, day: null });
    expect(tasks[1]).toMatchObject({ cadence: "every", every: 3, test: "pytest", lastQueued: "2026-10-27" });
    expect(tasks[2]).toMatchObject({ cadence: "monthly", day: 31, days: null });
  });
});
