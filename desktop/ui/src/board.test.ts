// The agent message board (DESKTOP_PARITY Phase 3 item 10): its switches and its discussions.
import { describe, expect, it } from "vitest";

import { boardOn, discussions, postTime, sqliteHome } from "./board";

const post = (id: string, root: string, at: number, channel = "retry") => ({ id, root, at, channel, author: "/root/a", text: id });

describe("the agent message board", () => {
  it("is on only with both features", () => {
    expect(boardOn({ features: { agent_message_board: true, multi_agent_v2: true } })).toBe(true);
    expect(boardOn({ features: { agent_message_board: true } })).toBe(false);
    expect(boardOn(null)).toBe(false);
    expect(sqliteHome({ sqlite_home: "/data/sqlite" })).toBe("/data/sqlite");
  });

  it("shows a post's time from its seconds (desktop/electron/src/board.ts), not as milliseconds", () => {
    expect(postTime(1_792_000_000)).toBe(new Date(1_792_000_000_000).toLocaleString());
    expect(postTime(1_792_000_000)).not.toContain("1970");
  });

  it("groups posts into discussions, the latest active first", () => {
    const posts = [post("r2", "p1", 50), post("p3", "p3", 40, "docs"), post("r1", "p1", 30), post("p1", "p1", 10), post("orphan", "gone", 5)];
    const grouped = discussions(posts, null);
    expect(grouped.map((entry) => [entry.root.id, entry.replies.map((reply) => reply.id)])).toEqual([["p1", ["r1", "r2"]], ["p3", []], ["orphan", []]]);
    expect(discussions(posts, "docs").map((entry) => entry.root.id)).toEqual(["p3"]);
  });
});
