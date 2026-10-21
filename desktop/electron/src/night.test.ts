// The Scheduled view's reading of Night Shift's files (DESKTOP_PARITY §2.12), in a scratch folder,
// and its queueing through a stand-in `ling night`.
import fs from "node:fs";
import os from "node:os";
import { nodeScript } from "./test-ling";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { addTask, dropTask, readReport, readReports, readTasks } from "./night";
import { taskDetails, transcriptOf } from "./night";

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
});

