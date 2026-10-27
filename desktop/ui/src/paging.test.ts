// Every thread reachable (DESKTOP §16 B10): the sidebar reads thread/list page by page, so a project
// whose threads are all older than the first page is still listed; and a list by date across projects.
import { describe, expect, it } from "vitest";

import type { Thread } from "./protocol/v2/Thread";
import { initialState, projects, reduce } from "./store";
import { chronological, readThreadPages } from "./threads";

const thread = (id: string, cwd: string, at: number) => ({ id, cwd, updatedAt: at, createdAt: at, recencyAt: null, ephemeral: false, parentThreadId: null, preview: id, name: null, turns: [], status: { type: "idle" } }) as unknown as Thread;

describe("every thread reachable", () => {
  it("reads 450 threads in three pages, and the oldest project is listed", async () => {
    // 449 recent threads in one project, then one old thread in another, newest first as the server lists them.
    const all = [...Array.from({ length: 449 }, (_, index) => thread(`r${index}`, "/w/busy", 10_000 - index)), thread("old", "/w/quiet", 1)];
    const asked: (string | null)[] = [];
    const list = async (cursor: string | null) => {
      asked.push(cursor);
      const start = cursor ? Number(cursor) : 0;
      return { data: all.slice(start, start + 200), nextCursor: start + 200 < all.length ? String(start + 200) : null };
    };
    let state = initialState;
    const done = await readThreadPages(list, (page) => { state = reduce(state, { type: "threads", list: page }); });
    expect(done).toEqual({ pages: 3, complete: true });
    expect(asked).toEqual([null, "200", "400"]);
    expect(Object.keys(state.threads)).toHaveLength(450);
    expect(projects(state).map((group) => group.cwd)).toContain("/w/quiet");
  });

  it("stops at its page limit, and when told to", async () => {
    const endless = async (cursor: string | null) => ({ data: [thread(`t${cursor}`, "/w", 1)], nextCursor: `${Number(cursor ?? 0) + 1}` });
    expect(await readThreadPages(endless, () => {}, 4)).toEqual({ pages: 4, complete: false });
    let pages = 0;
    expect(await readThreadPages(endless, () => pages++, 10, () => pages >= 2)).toEqual({ pages: 2, complete: false });
  });

  it("lists by date across projects, newest first", () => {
    expect(chronological([thread("a", "/x", 1), thread("b", "/y", 3), thread("c", "/x", 2)]).map((t) => t.id)).toEqual(["b", "c", "a"]);
  });
});
