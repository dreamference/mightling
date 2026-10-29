// The air-gap level in force for a thread, or the configured one: Work's title bar shows it and the
// permission picker disables Full Access at `on` (a courtesy; the refusal that binds is the
// server's, specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §8.2). Asked of `ling airgapped --json`, the one
// reader every component uses (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §6.5), never read here; the
// answers it accepts are the `answer` cases of ling-rs/airgapped/cases.json.

import { execFile, type ExecFileException } from "node:child_process";
import fs from "node:fs";
import { command } from "./platform";

import type { Airgapped } from "./api";

/** The source of a level `ling airgapped --json` did not give: `countsAsOn` takes it as `on`. */
const UNKNOWN: Airgapped = { level: "off", source: "unknown (ling airgapped did not answer)" };

/**
 * What `ling airgapped --json` answered: one JSON object whose `level` is `off` or `on`, from a run
 * that exited 0 (`status` null: it did not run or did not finish). Anything else is no answer.
 */
export function parseAnswer(stdout: string | null, status: number | null): Airgapped | null {
  if (status !== 0 || stdout === null) return null;
  let answer: unknown;
  try {
    answer = JSON.parse(stdout);
  } catch {
    return null;
  }
  if (typeof answer !== "object" || answer === null || Array.isArray(answer)) return null;
  const { level, source } = answer as { level?: unknown; source?: unknown };
  if (level !== "off" && level !== "on") return null;
  return { level, source: typeof source === "string" ? source : "" };
}

/** The exit status an `execFile` callback saw: 0, the command's own, or null when it did not run or was killed. */
function exitStatus(error: ExecFileException | null): number | null {
  if (!error) return 0;
  return typeof error.code === "number" && !error.killed ? error.code : null;
}

function threadArgs(thread: string | null): string[] {
  return ["airgapped", "--json", ...(thread && /^[A-Za-z0-9-]+$/.test(thread) ? ["--thread", thread] : [])];
}

export function airgapped(ling: string, thread: string | null): Promise<Airgapped> {
  return runStatus(ling, threadArgs(thread), null).then((answer) => answer ?? UNKNOWN);
}

/**
 * Whether a level refuses what would reach the network (a push, a link, a preview's site): `on`, or a
 * `ling airgapped` that did not answer, whose "unknown" source is no level at all. Fails closed, as
 * `airgappedOn` does; the launcher names no source of its own "unknown" (`Source::label`).
 */
export function countsAsOn(level: Airgapped): boolean {
  return level.level === "on" || level.source.startsWith("unknown");
}

/** One `ling airgapped --json [--thread <id>]` run in `cwd` (null: this process's folder); null when it did not answer. */
export type StatusRun = (ling: string, args: string[], cwd: string | null) => Promise<Airgapped | null>;

const runStatus: StatusRun = (ling, args, cwd) =>
  new Promise((resolve) => {
    const run = command(ling, args);
    execFile(run.file, run.args, { timeout: 10_000, shell: run.shell, windowsHide: true, ...(cwd ? { cwd } : {}) }, (error, stdout) => {
      resolve(parseAnswer(String(stdout), exitStatus(error)));
    });
  });

/**
 * Whether a thread is at `on`, for a refusal that binds (policy.json's `airgappedRefusedRequests`: a `!`
 * command runs in the user's shell with no sandbox). The launcher's own resolution, `ling airgapped
 * --thread <id>`, run in this process's folder and, when the thread's folder `cwd` is known, in that
 * one too, so its repository's `dreamference.toml` counts; the stricter wins. Unlike `airgapped`,
 * an answer that does not come counts as `on`: this gate fails closed.
 */
export async function airgappedOn(ling: string, thread: string | null, cwd: string | null, run: StatusRun = runStatus): Promise<boolean> {
  return countsAsOn(await airgappedLevel(ling, thread, cwd, run));
}

/**
 * The level a refusal reads, asked as `airgappedOn` asks (here, and in `cwd` when it is known, so a
 * project's `on` is seen): the stricter answer, one that did not come as "unknown" (`countsAsOn`).
 */
export async function airgappedLevel(ling: string, thread: string | null, cwd: string | null, run: StatusRun = runStatus): Promise<Airgapped> {
  const args = threadArgs(thread);
  const places: (string | null)[] = [null];
  if (cwd && fs.existsSync(cwd)) places.push(cwd);
  const answers = (await Promise.all(places.map((place) => run(ling, args, place))))
    .map((answer): Airgapped => answer ?? UNKNOWN);
  return answers.find(countsAsOn) ?? answers[answers.length - 1];
}

/** Sets the level new threads start at (`ling airgapped default <level>`); resolves with what it printed. */
export function setDefault(ling: string, level: "off" | "on"): Promise<string> {
  if (level !== "off" && level !== "on") return Promise.reject(new Error("the level is off or on"));
  return new Promise((resolve, reject) => {
    const run = command(ling, ["airgapped", "default", level]);
    execFile(run.file, run.args, { timeout: 10_000, shell: run.shell, windowsHide: true }, (error, stdout, stderr) => {
      if (error) reject(new Error(String(stderr || stdout || error.message).trim()));
      else resolve(String(stdout).trim());
    });
  });
}
