// The Scheduled view over Night Shift (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.12): Night
// Shift is Mightling's scheduler, and its queue and reports are files under `$CODEX_HOME/night/`
// (ling-rs/src/night.rs): `tasks/<id>.json`, one task each, replaced whole; `reports/<date>.md`,
// the morning report. This reads them, and queues or drops a task through `ling night add|drop`, the
// launcher's own command, so the window never writes a task file itself; Run now starts
// `ling-admin night run`, the runner, which gives way to a busy machine on its own. No Electron here.

import { execFile, spawn } from "node:child_process";
import { command } from "./platform";
import fs from "node:fs";
import path from "node:path";
import * as userFs from "./userfs";

export interface NightTask {
  id: string;
  repo: string;
  task: string;
  status: string;
  branch: string | null;
  test: string | null;
  on: string | null;
  /** When the status last changed (the history's last entry). */
  at: string | null;
  result: unknown;
}

const ID = /^[A-Za-z0-9_-]{1,80}$/;
const REPORT = /^[A-Za-z0-9_.-]{1,80}\.md$/;

const text = (value: unknown) => (typeof value === "string" ? value : null);

/** Every queued, running and finished task, newest first. */
export function readTasks(nightDir: string): NightTask[] {
  const dir = path.join(nightDir, "tasks");
  let names: string[];
  try {
    names = fs.readdirSync(dir).filter((name) => name.endsWith(".json"));
  } catch {
    return [];
  }
  const tasks: NightTask[] = [];
  for (const name of names.slice(-1000)) {
    try {
      const value = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8")) as Record<string, unknown>;
      const history = Array.isArray(value.history) ? (value.history as { at?: unknown }[]) : [];
      tasks.push({
        id: text(value.id) ?? name.slice(0, -5),
        repo: text(value.repo) ?? "",
        task: text(value.task) ?? "",
        status: text(value.status) ?? "unknown",
        branch: text(value.branch),
        test: text(value.test),
        on: text(value.on),
        at: text(history[history.length - 1]?.at) ?? null,
        result: value.result ?? null,
      });
    } catch {
      // A task being replaced, or not a task: skipped.
    }
  }
  return tasks.sort((a, b) => (b.at ?? b.id).localeCompare(a.at ?? a.id));
}

/** The morning reports, newest first. */
export function readReports(nightDir: string): { name: string; modified: number }[] {
  const dir = path.join(nightDir, "reports");
  try {
    return fs.readdirSync(dir).filter((name) => REPORT.test(name))
      .map((name) => ({ name, modified: fs.statSync(path.join(dir, name)).mtimeMs }))
      .sort((a, b) => b.modified - a.modified);
  } catch {
    return [];
  }
}

export function readReport(nightDir: string, name: unknown): string {
  if (typeof name !== "string" || !REPORT.test(name)) throw new Error("not a report");
  return fs.readFileSync(path.join(nightDir, "reports", name), "utf8").slice(0, 512 * 1024);
}

function run(ling: string, args: string[], cwd: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const started = command(ling, args);
    execFile(started.file, started.args, { cwd, timeout: 30_000, shell: started.shell, windowsHide: true }, (error, stdout, stderr) => {
      const output = String(stdout || stderr).trim();
      if (error && !output) reject(new Error(error.message));
      else if (error) reject(new Error(output));
      else resolve(output);
    });
  });
}

/** Queues a task for tonight in `repo` (`ling night add [--test <cmd>] <task>`); resolves with what it printed. */
export async function addTask(ling: string, repo: unknown, task: unknown, test: unknown): Promise<string> {
  // Asynchronous (userfs.ts): the project may be a folder macOS guards behind a prompt.
  if (typeof repo !== "string" || !path.isAbsolute(repo) || !(await userFs.isDirectory(repo))) throw new Error("name the project folder");
  if (typeof task !== "string" || !task.trim() || task.length > 8000) throw new Error("describe the task");
  const testArgs = typeof test === "string" && test.trim() ? ["--test", test.trim()] : [];
  return run(ling, ["night", "add", ...testArgs, task.trim()], repo);
}

/** Cancels a queued task (`ling night drop <id>`, run in its repository). */
export function dropTask(ling: string, repo: unknown, id: unknown): Promise<string> {
  if (typeof id !== "string" || !ID.test(id)) return Promise.reject(new Error("not a task"));
  if (typeof repo !== "string" || !path.isAbsolute(repo)) return Promise.reject(new Error("not a repository"));
  return run(ling, ["night", "drop", id], repo);
}

/** Run now: the runner (`ling-admin night run`), detached; it refuses on its own when the machine is busy. */
export function runNow(admin: string): void {
  const child = spawn(admin, ["night", "run"], { detached: true, stdio: "ignore" });
  child.on("error", () => {});
  child.unref();
}
