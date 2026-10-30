// The edges of the window routing (windows.ts) that windows.vectors.json does not reach: what the
// server sends that only looks tagged, the initialize requests held across a failed answer or a
// restart, a window that shows no thread, and a pane's requests, which stay with its own thread.
import { describe, expect, it } from "vitest";

import { PANE_TEXT_MAX, RequestRouter, WindowHub, paneAction } from "./windows";

describe("answers and requests from the server", () => {
  it("go to every window when they are requests, whatever their id looks like", () => {
    const router = new RequestRouter();
    const request = { method: "item/commandExecution/requestApproval", id: "7␟1", params: { threadId: "t1" } };
    expect(router.incoming(request)).toEqual({ to: "all", payload: request });
  });

  it("go to every window when their id is a string no window tagged", () => {
    const router = new RequestRouter();
    for (const id of ["12", "abc", "7␟not json"]) {
      expect(router.incoming({ id, result: {} }), id).toEqual({ to: "all", payload: { id, result: {} } });
    }
    expect(router.incoming({ id: "7␟12", result: {} })).toEqual({ to: "7", payload: { id: 12, result: {} } });
  });
});

describe("initialize requests held for the first window's answer", () => {
  it("are answered once: a failed answer goes to them, and the next answer does not again", () => {
    const router = new RequestRouter();
    const sent = router.outgoing("1", { method: "initialize", id: 1, params: {} }).send as { id: string };
    expect(router.outgoing("2", { method: "initialize", id: 5, params: {} })).toEqual({ send: null, reply: null });
    expect(router.incoming({ id: sent.id, error: { code: -1, message: "no" } })).toEqual({
      to: "1",
      payload: { id: 1, error: { code: -1, message: "no" } },
      also: [{ to: "2", payload: { id: 5, error: { code: -1, message: "no" } } }],
    });
    // Window 2 tries again: its initialize is the one sent now, and its answer goes to it alone.
    const again = router.outgoing("2", { method: "initialize", id: 6, params: {} }).send as { id: string };
    expect(router.incoming({ id: again.id, result: { userAgent: "ling" } })).toEqual({ to: "2", payload: { id: 6, result: { userAgent: "ling" } } });
  });

  it("are forgotten when the server goes away", () => {
    const router = new RequestRouter();
    router.outgoing("1", { method: "initialize", id: 1, params: {} });
    router.outgoing("2", { method: "initialize", id: 1, params: {} });
    router.reset();
    const sent = router.outgoing("3", { method: "initialize", id: 1, params: {} }).send as { id: string };
    expect(sent.id).toBe("3␟1");
    expect(router.incoming({ id: sent.id, result: {} })).toEqual({ to: "3", payload: { id: 1, result: {} } });
  });
});

describe("a window that shows no thread", () => {
  it("owns nothing", () => {
    const hub = new WindowHub();
    hub.open("1", "full");
    hub.show("1", null);
    hub.show("1", null, true);
    expect(hub.state("1").owners).toEqual({});
  });
});

describe("a detached pane", () => {
  it("may answer a server request that names no thread", () => {
    const hub = new WindowHub();
    hub.open("1", "full");
    hub.open("2", "pane");
    hub.incoming({ method: "account/chatgptAuthTokens/refresh", id: 3, params: {} });
    expect(hub.outgoing("2", { id: 3, result: {} }).send).toEqual({ id: 3, result: {} });
  });

  it("asks only for its own thread, whatever the request names", () => {
    expect(paneAction({ kind: "newTerminal", thread: "t2" }, "t1")).toEqual({ kind: "newTerminal", thread: "t1" });
    expect(paneAction({ kind: "compose", text: "x", thread: "t2" }, "t1")).toEqual({ kind: "compose", thread: "t1", text: "x" });
    expect(paneAction({ kind: "openFile", path: "a.ts", line: 3, thread: "t2" }, "t1")).toEqual({ kind: "openFile", thread: "t1", path: "a.ts", line: 3 });
  });

  it("hands the composer at most 256 KB, and opens a file by a name of at most 4096 characters", () => {
    expect(paneAction({ kind: "compose", text: "a".repeat(PANE_TEXT_MAX) }, "t1")).toEqual({ kind: "compose", thread: "t1", text: "a".repeat(PANE_TEXT_MAX) });
    expect(() => paneAction({ kind: "compose", text: "a".repeat(PANE_TEXT_MAX + 1) }, "t1")).toThrow("256 KB");
    expect(paneAction({ kind: "openFile", path: "a".repeat(4096), line: null }, "t1")).toMatchObject({ kind: "openFile", path: "a".repeat(4096) });
    expect(() => paneAction({ kind: "openFile", path: "a".repeat(4097) }, "t1")).toThrow("must be named");
  });

  it("opens a file at a line only when the line is a whole number", () => {
    expect(paneAction({ kind: "openFile", path: "a.ts", line: 1.5 }, "t1")).toEqual({ kind: "openFile", thread: "t1", path: "a.ts", line: null });
  });

  it("asks with an object, never a list", () => {
    expect(() => paneAction([{ kind: "newTerminal" }], "t1")).toThrow("must be an object");
  });
});
