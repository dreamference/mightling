// The Activity view (DESKTOP_PARITY Phase 3 item 5): what counts as something the agent did, and
// how the threads' timelines merge and filter.
import { describe, expect, it } from "vitest";

import { activityOf, dayLabel, describeItem, merged, type ActivityKind } from "./activity";
import type { Thread } from "./protocol/v2/Thread";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import type { Turn } from "./protocol/v2/Turn";

const thread = (id: string, cwd: string, name: string) => ({ id, cwd, name, preview: "", updatedAt: 0, ephemeral: false }) as unknown as Thread;
const turn = (id: string, startedAt: number, items: unknown[]) => ({ id, startedAt, completedAt: null, items }) as unknown as Turn;
const all = new Set<ActivityKind>(["command", "files", "tool", "web", "agents"]);

describe("activity", () => {
  it("keeps actions, not talk", () => {
    expect(describeItem({ type: "commandExecution", command: "pytest -q", exitCode: 1 } as unknown as ThreadItem)).toEqual({ kind: "command", summary: "Ran pytest -q (exit 1)", failed: true });
    expect(describeItem({ type: "fileChange", status: "completed", changes: [{ path: "/w/a.py", kind: { type: "update" } }, { path: "/w/b.py", kind: { type: "add" } }] } as unknown as ThreadItem)?.summary).toBe("Edited a.py, b.py");
    expect(describeItem({ type: "fileChange", status: "completed", changes: ["a", "b", "c", "d", "e"].map((n) => ({ path: `/w/${n}.md`, kind: { type: "add" } })) } as unknown as ThreadItem)?.summary).toBe("Added a.md, b.md, c.md and 2 more");
    expect(describeItem({ type: "mcpToolCall", server: "ling-code", tool: "refs", error: null } as unknown as ThreadItem)?.summary).toBe("Called ling-code.refs");
    expect(describeItem({ type: "agentMessage", text: "done" } as unknown as ThreadItem)).toBeNull();
    expect(describeItem({ type: "userMessage", content: [] } as unknown as ThreadItem)).toBeNull();
  });

  it("merges threads newest first (a turn's last item first), and filters by kind and project", () => {
    const a = activityOf(thread("a", "/w/site", "Hero"), [turn("t1", 100, [
      { type: "commandExecution", id: "c1", command: "npm test", exitCode: 0 },
      { type: "fileChange", id: "f1", status: "completed", changes: [{ path: "/w/site/a.ts", kind: { type: "update" } }] },
    ])]);
    const b = activityOf(thread("b", "/w/fetcher", "Retry"), [turn("t2", 200, [{ type: "commandExecution", id: "c2", command: "pytest", exitCode: 0 }])]);
    expect(merged([a, b], all, null).map((entry) => entry.key)).toEqual(["b/t2/c2", "a/t1/f1", "a/t1/c1"]);
    expect(merged([a, b], new Set(["files"]), null).map((entry) => entry.key)).toEqual(["a/t1/f1"]);
    expect(merged([a, b], all, "/w/fetcher").map((entry) => entry.thread)).toEqual(["Retry"]);
  });

  it("filters by a Windows project folder, its subfolders included", () => {
    const sub = activityOf(thread("w", "C:\\w\\site\\web", "Hero"), [turn("t1", 100, [{ type: "commandExecution", id: "c1", command: "npm test", exitCode: 0 }])]);
    const other = activityOf(thread("o", "C:\\w\\site2", "Other"), [turn("t2", 200, [{ type: "commandExecution", id: "c2", command: "npm test", exitCode: 0 }])]);
    expect(merged([sub, other], all, "C:\\w\\site").map((entry) => entry.thread)).toEqual(["Hero"]);
    expect(merged([sub, other], all, "C:\\w\\site\\").map((entry) => entry.thread)).toEqual(["Hero"]);
  });

  it("names the days", () => {
    const now = new Date(2026, 9, 18, 12).getTime();
    expect(dayLabel(now / 1000, now)).toBe("Today");
    expect(dayLabel(now / 1000 - 86_400, now)).toBe("Yesterday");
  });
});
