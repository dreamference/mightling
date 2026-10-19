// Threads read to be viewed, resumed once when a request needs them loaded (loader.ts).
import { describe, expect, it } from "vitest";

import { ThreadLoader } from "./loader";

describe("the thread loader", () => {
  it("resumes a thread once, before the first request that needs it, and never for one it started", async () => {
    const resumed: string[] = [];
    const loader = new ThreadLoader(async (threadId) => {
      resumed.push(threadId);
    });
    // Reading, listing and fetching the goal need nothing.
    expect(loader.before("thread/read", { threadId: "t1" })).toBeUndefined();
    expect(loader.before("thread/goal/get", { threadId: "t1" })).toBeUndefined();
    // A message, then another and a compaction at once: one resume.
    await Promise.all([loader.before("turn/start", { threadId: "t1" }), loader.before("turn/start", { threadId: "t1" }), loader.before("thread/compact/start", { threadId: "t1" })]);
    expect(resumed).toEqual(["t1"]);
    expect(loader.before("turn/start", { threadId: "t1" })).toBeUndefined();
    // A thread the window started or forked is loaded already.
    loader.after("thread/start", { thread: { id: "t2" } });
    loader.after("thread/fork", { thread: { id: "t3" } });
    expect(loader.before("turn/start", { threadId: "t2" })).toBeUndefined();
    expect(loader.before("review/start", { threadId: "t3" })).toBeUndefined();
    // A new server has nothing loaded.
    loader.reset();
    await loader.before("turn/steer", { threadId: "t2" });
    expect(resumed).toEqual(["t1", "t2"]);
  });

  it("lets a failed resume fail the request, and tries again next time", async () => {
    let fail = true;
    const loader = new ThreadLoader(async () => {
      if (fail) throw new Error("no such thread");
    });
    await expect(loader.before("turn/start", { threadId: "t1" })).rejects.toThrow("no such thread");
    fail = false;
    await loader.before("turn/start", { threadId: "t1" });
    expect(loader.isLoaded("t1")).toBe(true);
  });
});
