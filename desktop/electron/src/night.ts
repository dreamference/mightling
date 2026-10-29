// The Scheduled view over Night Shift (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.12): Night
// Shift is Mightling's scheduler, and its queue and reports are files under `$CODEX_HOME/night/`
// (ling-rs/src/night.rs): `tasks/<id>.json`, one task each, replaced whole; `reports/<date>.md`,
// the morning report. This reads them, and queues or drops a task through `ling night add|drop`, the
// launcher's own command, so the window never writes a task file itself; Run now starts
// `ling-admin night run`, the runner, which gives way to a busy machine on its own. No Electron here.

import { execFile, spawn } from "node:child_process";
import { command } from "./platform";
import { driverOverrides, gitEnv, hardenedArgs } from "./git";
import fs from "node:fs";
import path from "node:path";
import * as userFs from "./userfs";
import { CADENCES, type Cadence, cadenceArgs } from "./cadence";

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
  /** How many times the runner started it (DESKTOP §16 B40), and when `ling night apply` brought it into a checkout. */
  attempts: number | null;
  applied: string | null;
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
  // Past 1000 the newest are read, by when their file last changed: the folder lists them in no order.
  if (names.length > 1000) {
    const changed = new Map(names.map((name) => [name, fs.statSync(path.join(dir, name), { throwIfNoEntry: false })?.mtimeMs ?? 0]));
    names = names.sort((a, b) => (changed.get(b) ?? 0) - (changed.get(a) ?? 0)).slice(0, 1000);
  }
  const tasks: NightTask[] = [];
  for (const name of names) {
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
        attempts: typeof value.attempts === "number" ? value.attempts : null,
        applied: text((value.applied as { at?: unknown } | null)?.at),
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
      // A failure says why on stderr; a success's answer is on stdout.
      const output = String(error ? stderr.trim() || stdout : stdout || stderr).trim();
      if (error && !output) reject(new Error(error.message));
      else if (error) reject(new Error(output));
      else resolve(output);
    });
  });
}

/**
 * A node's name as `ling node list` shows it (the launcher's own rule, ling-rs/src/night.rs
 * `node_name`), and never an address: a node is found by its name when the task is handed over.
 */
export function nodeName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const name = value.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$/.test(name)) return null;
  if (/^\d+(\.\d+){3}$/.test(name)) return null;
  return name;
}

/** The most attempts the Queue form offers; the launcher takes any count of 1 or more. */
export const MAX_BEST_OF = 9;

/**
 * Queues a task for tonight in `repo` (`ling night add [--test <cmd>] [--on <node>] [--best-of N] <task>`);
 * resolves with what it printed. A node works a task sent to it once, so `on` and `bestOf` exclude
 * each other, as the launcher says.
 */
export async function addTask(ling: string, repo: unknown, task: unknown, test: unknown, on: unknown = null, bestOf: unknown = null): Promise<string> {
  // Asynchronous (userfs.ts): the project may be a folder macOS guards behind a prompt.
  if (typeof repo !== "string" || !path.isAbsolute(repo) || !(await userFs.isDirectory(repo))) throw new Error("name the project folder");
  if (typeof task !== "string" || !task.trim() || task.length > 8000) throw new Error("describe the task");
  const testArgs = typeof test === "string" && test.trim() ? ["--test", test.trim()] : [];
  const node = on === null || on === undefined || on === "" ? null : nodeName(on);
  if (on !== null && on !== undefined && on !== "" && !node) throw new Error("name a node as ling node list shows it, not an address");
  if (bestOf !== null && bestOf !== undefined && !(Number.isInteger(bestOf) && (bestOf as number) >= 1 && (bestOf as number) <= MAX_BEST_OF)) throw new Error(`best-of takes 1 to ${MAX_BEST_OF} attempts`);
  if (node && bestOf !== null && bestOf !== undefined) throw new Error("a node works a task sent to it once: choose a node or a number of attempts, not both");
  const nodeArgs = node ? ["--on", node] : [];
  const bestArgs = typeof bestOf === "number" ? ["--best-of", String(bestOf)] : [];
  // `--` is not needed: the launcher stops at the first word that is not one of its options, and the task is one argument.
  return run(ling, ["night", "add", ...testArgs, ...nodeArgs, ...bestArgs, task.trim()], repo);
}

/** The timer's unit file `ling-admin night enable` writes, and the comment in it that names the window. */
const TIMER = path.join(".config", "systemd", "user", "mightling-night.timer");
const WINDOW_MARKER = "# Night Shift window: ";

/**
 * Whether Night Shift runs by itself, and in which window: the timer `ling-admin night enable`
 * installs (dreamference/night_shift/night_shift_scheduler.py), read from its unit file as the
 * launcher's `/night list` reads it. Nothing is run. Elsewhere than Linux there is no timer.
 */
export async function nightStatus(home: string, platform: NodeJS.Platform = process.platform): Promise<{ timer: "on" | "off" | "unsupported"; window: string | null }> {
  if (platform !== "linux") return { timer: "unsupported", window: null };
  let unit: string;
  try {
    unit = await fs.promises.readFile(path.join(home, TIMER), "utf8");
  } catch {
    return { timer: "off", window: null };
  }
  const line = unit.split("\n").find((entry) => entry.startsWith(WINDOW_MARKER));
  const window = line ? line.slice(WINDOW_MARKER.length).trim() : "";
  return { timer: "on", window: /^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$/.test(window) ? window : null };
}

/** Cancels a queued task (`ling night drop <id>`, run in its repository). */
export function dropTask(ling: string, repo: unknown, id: unknown): Promise<string> {
  if (typeof id !== "string" || !ID.test(id)) return Promise.reject(new Error("not a task"));
  if (typeof repo !== "string" || !path.isAbsolute(repo)) return Promise.reject(new Error("not a repository"));
  return run(ling, ["night", "drop", id], repo);
}

/**
 * Apply (DESKTOP §16 B40): `ling night apply <id>` in the checkout `cwd`, the thread's own folder,
 * which brings the task's branch there as uncommitted changes, all or nothing (ling-rs/src/night.rs).
 * Only git on this machine: nothing reaches the network.
 */
export async function applyTask(ling: string, cwd: unknown, id: unknown): Promise<string> {
  if (typeof id !== "string" || !ID.test(id)) throw new Error("not a task");
  if (typeof cwd !== "string" || !path.isAbsolute(cwd) || !(await userFs.isDirectory(cwd))) throw new Error("name the thread's folder");
  return run(ling, ["night", "apply", id], cwd);
}

/**
 * A task's block in a morning report (night_shift_report.py `_task_block`): from its `### <id> — `
 * heading up to the next heading, or null when the report does not name it. Pure.
 */
export function reportBlock(report: string, id: string): string | null {
  const lines = report.split("\n");
  const start = lines.findIndex((line) => line.startsWith(`### ${id} `));
  if (start < 0) return null;
  const end = lines.findIndex((line, index) => index > start && /^#{1,3} /.test(line));
  return lines.slice(start, end < 0 ? lines.length : end).join("\n").trim();
}

/** The reports `taskNote` looks through, newest first: a task finishes within a few nights of being queued. */
const NOTE_REPORTS = 30;

/** A task's note from the newest morning report that names it (DESKTOP §16 B40): the report's name and the block. */
export function taskNote(nightDir: string, id: unknown): { report: string; text: string } | null {
  if (typeof id !== "string" || !ID.test(id)) throw new Error("not a task");
  for (const { name } of readReports(nightDir).slice(0, NOTE_REPORTS)) {
    let block: string | null = null;
    try {
      block = reportBlock(readReport(nightDir, name), id);
    } catch {
      // A report being written: the next one, or the next look.
    }
    if (block) return { report: name, text: block };
  }
  return null;
}

/** Run now: the runner (`ling-admin night run`), detached; it refuses on its own when the machine is busy. */
export function runNow(admin: string): void {
  const child = spawn(admin, ["night", "run"], { detached: true, stdio: "ignore" });
  child.on("error", () => {});
  child.unref();
}

/** What a finished task left (DESKTOP_PARITY §4.5 item 18, the owner's decision 7): its last message, its diff, its transcript. */
export interface NightDetails {
  result: string | null;
  diff: string | null;
  transcript: string | null;
  /** The `ling exec` session the run was, when its log names it: opened as a thread in Work. */
  session: string | null;
}

const DETAIL_LIMIT = 512 * 1024;

function readBounded(file: string): string | null {
  try {
    const text = fs.readFileSync(file, "utf8");
    return text.length > DETAIL_LIMIT ? `${text.slice(0, DETAIL_LIMIT)}\n[cut at ${DETAIL_LIMIT / 1024} KB]` : text;
  } catch {
    return null;
  }
}

/** A `ling exec --json` log as a transcript: the agent's messages, the commands with their exit, the files changed. */
export function transcriptOf(log: string): { text: string; session: string | null } {
  const lines: string[] = [];
  let session: string | null = null;
  for (const raw of log.split("\n")) {
    if (!raw.trim()) continue;
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      continue;
    }
    if (event.type === "thread.started" && typeof event.thread_id === "string") session = event.thread_id;
    if (event.type !== "item.completed" || typeof event.item !== "object" || event.item === null) continue;
    const item = event.item as Record<string, unknown>;
    if (item.type === "agent_message" && typeof item.text === "string") lines.push(`Mightling: ${item.text}`);
    else if (item.type === "command_execution" && typeof item.command === "string") {
      lines.push(`$ ${item.command}${typeof item.exit_code === "number" ? `  (exit ${item.exit_code})` : ""}`);
    } else if (item.type === "file_change" && Array.isArray(item.changes)) {
      lines.push(`Changed ${(item.changes as { path?: unknown }[]).map((change) => String(change.path ?? "")).filter(Boolean).join(", ")}`);
    }
  }
  const text = lines.join("\n\n");
  return { text: text.length > DETAIL_LIMIT ? `${text.slice(0, DETAIL_LIMIT)}\n[cut]` : text, session };
}

/** The diff of a task's branch against the commit it started from, from git in its repository. */
function branchDiff(repo: string, base: string, branch: string): Promise<string | null> {
  return new Promise((resolve) => {
    void driverOverrides(repo).then((drivers) => {
      execFile("git", hardenedArgs(["diff", "--no-color", `${base}..${branch}`], drivers), { cwd: repo, timeout: 15_000, maxBuffer: DETAIL_LIMIT * 2, env: gitEnv() }, (error, stdout) => {
        resolve(error ? null : stdout.length > DETAIL_LIMIT ? `${stdout.slice(0, DETAIL_LIMIT)}\n[cut]` : stdout);
      });
    });
  });
}

/** A finished task's details: of the chosen attempt when best-of-N ran, else of the single run. */
export async function taskDetails(nightDir: string, id: unknown): Promise<NightDetails> {
  if (typeof id !== "string" || !ID.test(id)) throw new Error("not a task");
  const value = JSON.parse(fs.readFileSync(path.join(nightDir, "tasks", `${id}.json`), "utf8")) as Record<string, unknown>;
  const result = (typeof value.result === "object" && value.result !== null ? value.result : {}) as Record<string, unknown>;
  const best = (typeof result.best_of === "object" && result.best_of !== null ? result.best_of : null) as { chosen?: unknown } | null;
  const suffix = best && typeof best.chosen === "number" ? `-a${best.chosen}` : "";
  const logs = path.join(nightDir, "logs");
  const last = readBounded(path.join(logs, `${id}${suffix}.last.txt`)) ?? readBounded(path.join(logs, `${id}.last.txt`));
  const log = readBounded(path.join(logs, `${id}${suffix}.jsonl`)) ?? readBounded(path.join(logs, `${id}.jsonl`));
  const transcript = log ? transcriptOf(log) : { text: "", session: null };
  const repo = text(value.repo);
  const base = text(value.base);
  const branch = text(result.branch) ?? text(value.branch);
  const diff = readBounded(path.join(logs, `${id}${suffix}.diff`))
    ?? (repo && base && branch && path.isAbsolute(repo) ? await branchDiff(repo, base, branch) : null);
  return { result: last?.trim() || null, diff: diff || null, transcript: transcript.text || null, session: text(value.session) ?? transcript.session };
}

/** A repeating task (ling-admin night every; dreamference/night_shift/night_shift_recurring.py). */
export interface RepeatingTask {
  id: string;
  repo: string;
  task: string;
  test: string | null;
  cadence: Cadence;
  weekday: string | null;
  days: string[] | null;
  every: number | null;
  day: number | null;
  lastQueued: string | null;
  lastError: string | null;
}

const TEMPLATE = /^r[0-9a-f]{12}$/;

/** Repeats a task in `repo` every night, every week on one day, on chosen weekdays, every N nights or on a day of the month (`ling-admin night every add`); `cadence` and `options` are checked by cadence.ts. */
export async function addRepeating(admin: string, repo: unknown, task: unknown, test: unknown, cadence: unknown, options: unknown): Promise<string> {
  if (typeof repo !== "string" || !path.isAbsolute(repo) || !(await userFs.isDirectory(repo))) throw new Error("name the project folder");
  if (typeof task !== "string" || !task.trim() || task.length > 8000) throw new Error("describe the task");
  const when = cadenceArgs(cadence, options);
  const testArgs = typeof test === "string" && test.trim() ? ["--test", test.trim()] : [];
  return run(admin, ["night", "every", "add", ...when, ...testArgs, "--", task.trim()], repo);
}

/** The repeating tasks (`ling-admin night every list`, one JSON object a line); a cadence this window does not know is left out. */
export async function listRepeating(admin: string): Promise<RepeatingTask[]> {
  const output = await run(admin, ["night", "every", "list"], process.env.HOME ?? "/");
  const found: RepeatingTask[] = [];
  for (const line of output.split("\n")) {
    try {
      const value = JSON.parse(line) as Record<string, unknown>;
      if (typeof value.id !== "string" || !TEMPLATE.test(value.id)) continue;
      const cadence = CADENCES.find((known) => known === value.cadence);
      if (!cadence) continue;
      const whole = (number: unknown) => (Number.isInteger(number) ? (number as number) : null);
      found.push({
        id: value.id, repo: text(value.repo) ?? "", task: text(value.task) ?? "", test: text(value.test),
        cadence, weekday: text(value.weekday),
        days: Array.isArray(value.days) ? value.days.filter((day): day is string => typeof day === "string") : null,
        every: whole(value.every), day: whole(value.day),
        lastQueued: text(value.last_queued), lastError: text(value.last_error),
      });
    } catch {
      // Not a template line.
    }
  }
  return found;
}

/** Stops repeating a task (`ling-admin night every drop <id>`). */
export function dropRepeating(admin: string, id: unknown): Promise<string> {
  if (typeof id !== "string" || !TEMPLATE.test(id)) return Promise.reject(new Error("not a repeating task"));
  return run(admin, ["night", "every", "drop", id], process.env.HOME ?? "/");
}
