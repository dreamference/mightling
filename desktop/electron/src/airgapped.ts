// The air-gap level in force for a thread, or the configured one: Work's title bar shows it and the
// permission picker disables Full Access at `on` (a courtesy; the refusal that binds is the
// server's, specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §8.2). Asked of `ling airgapped --thread <id>`,
// the launcher's own resolver, rather than kept as a fourth copy of the resolver crate.

import { execFile } from "node:child_process";
import fs from "node:fs";
import { command } from "./platform";

import type { Airgapped } from "./api";

/** The first line `ling airgapped` prints: `Airgapped: <level> (<source>)`. */
export function parseStatus(text: string): Airgapped | null {
  const match = text.match(/^Airgapped:\s+(off|on)\s+\((.*)\)\s*$/m);
  return match ? { level: match[1] as Airgapped["level"], source: match[2] } : null;
}

export function airgapped(ling: string, thread: string | null): Promise<Airgapped> {
  const args = ["airgapped", ...(thread && /^[A-Za-z0-9-]+$/.test(thread) ? ["--thread", thread] : [])];
  return new Promise((resolve) => {
    const run = command(ling, args);
    execFile(run.file, run.args, { timeout: 10_000, shell: run.shell, windowsHide: true }, (_error, stdout) => {
      resolve(parseStatus(String(stdout)) ?? { level: "off", source: "unknown (ling airgapped did not answer)" });
    });
  });
}

/**
 * Whether a level refuses what would reach the network (a push, a link, a preview's site): `on`, or a
 * `ling airgapped` that did not answer, whose "unknown" source is no level at all. Fails closed, as
 * `airgappedOn` does; the launcher names no source of its own "unknown" (`Source::label`).
 */
export function countsAsOn(level: Airgapped): boolean {
  return level.level === "on" || level.source.startsWith("unknown");
}

/** One `ling airgapped [--thread <id>]` run in `cwd` (null: this process's folder); null when it did not answer. */
export type StatusRun = (ling: string, args: string[], cwd: string | null) => Promise<Airgapped | null>;

const runStatus: StatusRun = (ling, args, cwd) =>
  new Promise((resolve) => {
    const run = command(ling, args);
    execFile(run.file, run.args, { timeout: 10_000, shell: run.shell, windowsHide: true, ...(cwd ? { cwd } : {}) }, (_error, stdout) => {
      resolve(parseStatus(String(stdout)));
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
  const args = ["airgapped", ...(thread && /^[A-Za-z0-9-]+$/.test(thread) ? ["--thread", thread] : [])];
  const places: (string | null)[] = [null];
  if (cwd && fs.existsSync(cwd)) places.push(cwd);
  const answers = (await Promise.all(places.map((place) => run(ling, args, place))))
    .map((answer): Airgapped => answer ?? { level: "off", source: "unknown (ling airgapped did not answer)" });
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
