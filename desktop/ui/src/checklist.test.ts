// The agent's plan as a checklist (DESKTOP §16 B3): `turn/plan/updated` kept on its turn, replaced in
// place by the next update, and kept across a snapshot of the thread that does not carry it.
import { describe, expect, it } from "vitest";

import { checklistCount } from "./PlanChecklist";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { Thread } from "./protocol/v2/Thread";
import { initialState, mergedTurns, reduce, type Action, type WorkState } from "./store";

const thread = { id: "t", cwd: "/p", updatedAt: 1, createdAt: 1, ephemeral: false, parentThreadId: null, preview: "", name: null, turns: [], status: { type: "idle" } } as unknown as Thread;
const note = (method: string, params: object): Action => ({ type: "notification", notification: { method, params } as unknown as ServerNotification });
const run = (actions: Action[], state: WorkState = initialState) => actions.reduce(reduce, state);

describe("the plan checklist", () => {
  it("follows turn/plan/updated on its turn, each update replacing the last", () => {
    const state = run([
      { type: "opened", thread },
      note("turn/started", { threadId: "t", turn: { id: "u1", items: [], status: "inProgress", error: null } }),
      note("turn/plan/updated", { threadId: "t", turnId: "u1", explanation: null, plan: [{ step: "Read the client", status: "inProgress" }, { step: "Fix the retry", status: "pending" }] }),
      note("turn/plan/updated", { threadId: "t", turnId: "u1", explanation: " Found it early. ", plan: [{ step: "Read the client", status: "completed" }, { step: "Fix the retry", status: "inProgress" }] }),
    ]);
    const turn = state.threads.t.turns[0];
    expect(turn.checklist).toEqual({ explanation: "Found it early.", steps: [{ step: "Read the client", status: "completed" }, { step: "Fix the retry", status: "inProgress" }] });
    expect(checklistCount(turn.checklist!)).toBe("1 of 2 done");
    // It stays with the finished turn.
    const done = run([note("turn/completed", { threadId: "t", turn: { id: "u1", items: [], status: "completed", error: null } })], state);
    expect(done.threads.t.turns[0].checklist?.steps).toHaveLength(2);
    expect(done.threads.t.activeTurnId).toBeNull();
  });

  it("is kept when a snapshot of the thread is laid over it", () => {
    const known = [{ id: "u1", status: "completed" as const, items: [], error: null, diff: null, checklist: { explanation: null, steps: [{ step: "a", status: "completed" as const }] } }];
    const snapshot = [{ id: "u1", status: "completed" as const, items: [], error: null, diff: null }];
    expect(mergedTurns(snapshot, known)[0].checklist?.steps).toEqual([{ step: "a", status: "completed" }]);
  });
});
