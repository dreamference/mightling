// Settings › Profile (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.15, Mightling's): activity
// statistics read from the local session files under `$CODEX_HOME/sessions/YYYY/MM/DD/`, nothing
// sent anywhere: sessions, lifetime and peak-day tokens, the current and the longest streak of days
// with a session, the longest session. Each file is read only at its end, where the last token count
// is, so a long history costs little. No Electron here.

import fs from "node:fs";
import path from "node:path";

export interface Insights {
  sessions: number;
  days: number;
  lifetimeTokens: number;
  peakDay: { day: string; tokens: number } | null;
  currentStreak: number;
  longestStreak: number;
  longestSession: { file: string; minutes: number } | null;
}

const TAIL = 128 * 1024;
const MAX_FILES = 5000;

/** The last total token count in a session file's tail, or 0. */
export function lastTotal(text: string): number {
  const matches = [...text.matchAll(/"total_token_usage":\{[^}]*"total_tokens":(\d+)/g)];
  return matches.length ? Number(matches[matches.length - 1][1]) : 0;
}

/** The time a rollout file's name carries (`rollout-2026-10-14T12-31-38-…`), in ms. */
export function startedAt(name: string): number | null {
  const match = name.match(/rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})/);
  if (!match) return null;
  const [, y, mo, d, h, mi, s] = match.map(Number);
  return new Date(y, mo - 1, d, h, mi, s).getTime();
}

function readTail(file: string, size: number): string {
  const handle = fs.openSync(file, "r");
  try {
    const length = Math.min(size, TAIL);
    const buffer = Buffer.alloc(length);
    fs.readSync(handle, buffer, 0, length, size - length);
    return buffer.toString("utf8");
  } finally {
    fs.closeSync(handle);
  }
}

const dayOf = (ms: number) => {
  const date = new Date(ms);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
};

/** Consecutive-day runs over a set of days: the run ending today (or yesterday) and the longest. */
export function streaks(days: string[], today: string): { current: number; longest: number } {
  const set = new Set(days);
  const sorted = [...set].sort();
  let longest = 0;
  let run = 0;
  let previous: number | null = null;
  for (const day of sorted) {
    const ms = Date.parse(`${day}T12:00:00`);
    run = previous !== null && Math.round((ms - previous) / 86_400_000) === 1 ? run + 1 : 1;
    longest = Math.max(longest, run);
    previous = ms;
  }
  let current = 0;
  let cursor = Date.parse(`${today}T12:00:00`);
  if (!set.has(today)) cursor -= 86_400_000;
  while (set.has(dayOf(cursor))) {
    current++;
    cursor -= 86_400_000;
  }
  return { current, longest };
}

export function insights(codexHome: string, now: number = Date.now()): Insights {
  const root = path.join(codexHome, "sessions");
  const files: string[] = [];
  const walk = (dir: string, depth: number) => {
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    // newest first (the folders are YYYY/MM/DD, the files start with their time), so the cap drops the oldest
    entries.sort((a, b) => (a.name < b.name ? 1 : a.name > b.name ? -1 : 0));
    for (const entry of entries) {
      if (files.length >= MAX_FILES) return;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory() && depth < 3) walk(full, depth + 1);
      else if (entry.isFile() && entry.name.startsWith("rollout-") && entry.name.endsWith(".jsonl")) files.push(full);
    }
  };
  walk(root, 0);
  const perDay = new Map<string, number>();
  let lifetime = 0;
  let longest: Insights["longestSession"] = null;
  for (const file of files) {
    const stat = fs.statSync(file, { throwIfNoEntry: false });
    if (!stat) continue;
    const started = startedAt(path.basename(file)) ?? stat.mtimeMs;
    const tokens = lastTotal(readTail(file, stat.size));
    lifetime += tokens;
    const day = dayOf(started);
    perDay.set(day, (perDay.get(day) ?? 0) + tokens);
    const minutes = Math.max(0, Math.round((stat.mtimeMs - started) / 60_000));
    if (!longest || minutes > longest.minutes) longest = { file: path.basename(file), minutes };
  }
  let peak: Insights["peakDay"] = null;
  for (const [day, tokens] of perDay) if (!peak || tokens > peak.tokens) peak = { day, tokens };
  const { current, longest: longestStreak } = streaks([...perDay.keys()], dayOf(now));
  return { sessions: files.length, days: perDay.size, lifetimeTokens: lifetime, peakDay: peak, currentStreak: current, longestStreak, longestSession: longest };
}

/** The personal instructions every thread on this machine reads: `$CODEX_HOME/AGENTS.md`. */
export const PERSONAL_MAX = 256 * 1024;

export function readPersonal(codexHome: string): string {
  try {
    return fs.readFileSync(path.join(codexHome, "AGENTS.md"), "utf8");
  } catch {
    return "";
  }
}

export function writePersonal(codexHome: string, text: unknown): void {
  if (typeof text !== "string" || text.length > PERSONAL_MAX) throw new Error("the instructions must be text of at most 256 KB");
  fs.mkdirSync(codexHome, { recursive: true });
  const file = path.join(codexHome, "AGENTS.md");
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, text);
  fs.renameSync(temporary, file);
}
