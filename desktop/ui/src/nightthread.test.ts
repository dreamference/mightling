// Night Shift tasks inside a Work thread (DESKTOP §16 B40): the tie kept as a window preference, and
// what a card shows of a task at each stage.
import { describe, expect, it } from "vitest";

import type { NightTask } from "./bridge";
import { cardOf, cleanNightThreads, NIGHT_THREADS_MAX, noteBody, queuedId, reportDate, tasksOf, tieTask } from "./nightthread";

const SAID = "Queued 20261027-2200-abc on branch night/20261027-2200-abc, from 1a2b3c4d5e.\nTest command: detected in the worktree when the task runs.";

const task = (status: string, extra: Partial<NightTask> = {}): NightTask => ({
  id: "20261027-2200-abc", repo: "/w/app", task: "Make the retry test deterministic\n\nContext: this was asked in the conversation \"x\".", status,
  branch: "night/20261027-2200-abc", test: null, on: null, at: "2026-10-28T03:00:00+01:00", result: null, ...extra,
});

describe("the tie between a task and its thread", () => {
  it("reads the id ling night add printed, and nothing else", () => {
    expect(queuedId(SAID)).toBe("20261027-2200-abc");
    expect(queuedId("Night Shift: this repository has no commit yet, so there is nothing to start from.")).toBeNull();
    expect(queuedId("Queued ../x on branch night/x")).toBeNull();
  });

  it("records the task for the thread and lists a thread's tasks in the order queued", () => {
    let ties = tieTask({}, SAID, "thread-1");
    expect(ties).toEqual({ "20261027-2200-abc": "thread-1" });
    ties = tieTask(ties, "Queued 20261027-0900-001 on branch night/20261027-0900-001, from 1a.", "thread-1");
    ties = tieTask(ties, "Queued 20261027-1000-002 on branch night/20261027-1000-002, from 1a.", "thread-2");
    expect(tasksOf(ties, "thread-1")).toEqual(["20261027-0900-001", "20261027-2200-abc"]);
    expect(tasksOf(ties, "thread-2")).toEqual(["20261027-1000-002"]);
    expect(tasksOf(ties, "thread-3")).toEqual([]);
    // A refusal names no task: nothing is recorded.
    expect(tieTask(ties, "Usage: /night add <task>", "thread-1")).toEqual(ties);
  });

  it("keeps only well-formed entries and drops the oldest past the bound", () => {
    expect(cleanNightThreads({ "a b": "t", ok: "t", empty: "", num: 3 })).toEqual({ ok: "t" });
    expect(cleanNightThreads(["x"])).toEqual({});
    expect(cleanNightThreads(null)).toEqual({});
    let ties = {};
    for (let index = 0; index < NIGHT_THREADS_MAX + 5; index++) ties = tieTask(ties, `Queued 2026${String(index).padStart(6, "0")} on branch x`, "t");
    expect(Object.keys(ties)).toHaveLength(NIGHT_THREADS_MAX);
    expect(ties).not.toHaveProperty("2026000000");
    expect(ties).toHaveProperty(`2026${String(NIGHT_THREADS_MAX + 4).padStart(6, "0")}`);
  });
});

describe("a card", () => {
  it("shows a task the list does not have yet as queued", () => {
    expect(cardOf("20261027-2200-abc", undefined)).toMatchObject({ status: "queued", ended: false, state: "Queued for tonight", canApply: false });
  });

  it("shows a queued or running task by its first line, with nothing to apply", () => {
    expect(cardOf("20261027-2200-abc", task("queued"))).toMatchObject({ title: "Make the retry test deterministic", ended: false, attempts: null, branch: null, canApply: false });
    expect(cardOf("20261027-2200-abc", task("running", { attempts: 1 }))).toMatchObject({ state: "Running now", attempts: "1 attempt", ended: false });
    expect(cardOf("20261027-2200-abc", task("interrupted"))).toMatchObject({ ended: false });
  });

  it("shows a finished task's attempts, the chosen branch, and offers Apply only with a branch", () => {
    const done = cardOf("20261027-2200-abc", task("done", { result: { branch: "night/20261027-2200-abc", best_of: { n: 3, ran: 3, chosen: 2 } }, applied: "2026-10-28T09:00:00+01:00" }));
    expect(done).toMatchObject({ ended: true, state: "Done", attempts: "Best of 3: 3 ran, attempt 2 chosen", branch: "night/20261027-2200-abc", canApply: true, applied: "2026-10-28T09:00:00+01:00" });
    expect(cardOf("x", task("done", { result: { branch: "night/x", attempts: 2 } })).attempts).toBe("2 attempts");
    expect(cardOf("x", task("stalled", { result: { best_of: { n: 3, ran: 1, chosen: null } } }))).toMatchObject({ attempts: "Best of 3: 1 ran, none chosen", canApply: false, ended: true });
    expect(cardOf("x", task("no-change", { result: {} }))).toMatchObject({ ended: true, state: "Done, with nothing to change", canApply: false });
  });

  it("puts the report's block under its date, without the heading the card already shows", () => {
    expect(reportDate("2026-10-28.md")).toBe("2026-10-28");
    expect(noteBody("### 20261027-2200-abc — done\n\n- Task: x\n- Branch: `night/x`")).toBe("- Task: x\n- Branch: `night/x`");
  });
});
