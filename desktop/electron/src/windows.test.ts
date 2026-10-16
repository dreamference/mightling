// Several windows on one app-server (DESKTOP_PARITY §2.1 Pop-out): request ids tagged per window and
// answered to the window that asked, everything else to all, one initialize per connection.
import { describe, expect, it } from "vitest";

import { RequestRouter } from "./windows";

describe("the request router", () => {
  it("tags each window's requests and gives the answers back to the window that asked", () => {
    const router = new RequestRouter();
    const first = router.outgoing("1", { id: 1, method: "initialize", params: {} });
    expect(first.reply).toBeNull();
    const sent = first.send as { id: string };
    expect(sent.id).not.toBe(1);
    expect(router.incoming({ id: sent.id, result: { userAgent: "ling" } })).toEqual({ to: "1", payload: { id: 1, result: { userAgent: "ling" } } });
    expect(router.outgoing("1", { method: "initialized" })).toEqual({ send: { method: "initialized" }, reply: null });

    // A pop-out's initialize is answered here; its initialized is not sent twice.
    expect(router.outgoing("2", { id: 1, method: "initialize", params: {} })).toEqual({ send: null, reply: { id: 1, result: { userAgent: "ling" } } });
    expect(router.outgoing("2", { method: "initialized" })).toEqual({ send: null, reply: null });

    // Both windows use id 7; each answer finds its own window.
    const a = router.outgoing("1", { id: 7, method: "thread/read", params: {} }).send as { id: string };
    const b = router.outgoing("2", { id: 7, method: "thread/read", params: {} }).send as { id: string };
    expect(a.id).not.toBe(b.id);
    expect(router.incoming({ id: b.id, result: { thread: "b" } })).toEqual({ to: "2", payload: { id: 7, result: { thread: "b" } } });
    expect(router.incoming({ id: a.id, error: { code: 1, message: "no" } })).toEqual({ to: "1", payload: { id: 7, error: { code: 1, message: "no" } } });
  });

  it("sends notifications and the server's own requests to every window, and answers to them unchanged", () => {
    const router = new RequestRouter();
    expect(router.incoming({ method: "turn/started", params: {} }).to).toBe("all");
    expect(router.incoming({ id: "ask-1", method: "item/commandExecution/requestApproval", params: {} }).to).toBe("all");
    expect(router.outgoing("2", { id: "ask-1", result: { decision: "accept" } }).send).toEqual({ id: "ask-1", result: { decision: "accept" } });
  });
});
