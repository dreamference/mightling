// Settings › Profile and Personalization (DESKTOP_PARITY §2.15): statistics read from scratch session
// files, never the real ones, and the personal AGENTS.md.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import { insights, lastTotal, readPersonal, startedAt, streaks, writePersonal } from "./insights";

const token = (total: number) => JSON.stringify({ type: "event_msg", payload: { type: "token_count", info: { total_token_usage: { input_tokens: total, total_tokens: total } } } });

function session(home: string, day: string, time: string, totals: number[], minutes: number): void {
  const [y, m, d] = day.split("-");
  const dir = path.join(home, "sessions", y, m, d);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `rollout-${day}T${time}-0199aaaa-bbbb.jsonl`);
  fs.writeFileSync(file, totals.map(token).join("\n") + "\n");
  const start = startedAt(path.basename(file)) as number;
  fs.utimesSync(file, new Date(start + minutes * 60_000), new Date(start + minutes * 60_000));
}

describe("the profile's statistics", () => {
  it("count sessions, tokens, the peak day, streaks and the longest session from the files' ends", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-insights-"));
    session(home, "2026-10-14", "09-00-00", [100, 1500], 30);
    session(home, "2026-10-15", "10-00-00", [4000], 95);
    session(home, "2026-10-16", "11-00-00", [200], 5);
    session(home, "2026-10-16", "15-00-00", [300], 10);
    session(home, "2026-10-01", "08-00-00", [50], 1);
    const result = insights(home, Date.parse("2026-10-16T18:00:00"));
    expect(result).toMatchObject({ sessions: 5, days: 4, lifetimeTokens: 6050, peakDay: { day: "2026-10-15", tokens: 4000 }, currentStreak: 3, longestStreak: 3 });
    expect(result.longestSession?.minutes).toBe(95);
    expect(lastTotal('"total_token_usage":{"total_tokens":7} … "total_token_usage":{"input_tokens":1,"total_tokens":9}')).toBe(9);
    expect(streaks(["2026-10-10", "2026-10-11"], "2026-10-12")).toEqual({ current: 2, longest: 2 });
    expect(streaks(["2026-10-10"], "2026-10-12")).toEqual({ current: 0, longest: 1 });
    expect(insights(path.join(home, "none")).sessions).toBe(0);
  });

  it("keep the newest sessions when there are more than they read", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-insights-many-"));
    const old = path.join(home, "sessions", "2025", "01", "01");
    fs.mkdirSync(old, { recursive: true });
    for (let n = 0; n < 5000; n++) fs.writeFileSync(path.join(old, `rollout-2025-01-01T00-00-00-${String(n).padStart(4, "0")}.jsonl`), "");
    session(home, "2026-10-16", "11-00-00", [200], 5);
    // the folder listed oldest first, as a file system may
    const readdir = fs.readdirSync;
    vi.spyOn(fs, "readdirSync").mockImplementation(((dir: fs.PathLike, options: never) => (readdir(dir, options) as unknown as fs.Dirent[]).sort((a, b) => a.name.localeCompare(b.name))) as typeof fs.readdirSync);
    try {
      expect(insights(home, Date.parse("2026-10-16T18:00:00"))).toMatchObject({ lifetimeTokens: 200, currentStreak: 1 });
    } finally {
      vi.restoreAllMocks();
      fs.rmSync(home, { recursive: true, force: true });
    }
  });

  it("read and write the personal AGENTS.md", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-personal-"));
    expect(readPersonal(home)).toBe("");
    writePersonal(home, "Answer in British English.\n");
    expect(readPersonal(home)).toBe("Answer in British English.\n");
    expect(() => writePersonal(home, 42)).toThrow("at most 256 KB");
  });
});
