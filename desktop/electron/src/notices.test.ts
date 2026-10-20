// Which of the server's messages become desktop notifications (DESKTOP_PARITY §2.1, §2.15).
import { describe, expect, it } from "vitest";

import { Notices } from "./notices";

describe("notifications", () => {
  it("read the preferences only for a message that may notify", () => {
    let reads = 0;
    const notices = new Notices();
    notices.take({ method: "item/agentMessage/delta", params: { threadId: "t", delta: "x" } }, () => { reads++; return {}; });
    expect(reads).toBe(0);
    notices.take({ method: "turn/completed", params: { threadId: "t", turn: { status: "completed" } } }, () => { reads++; return {}; });
    expect(reads).toBe(1);
  });

  it("come for a finished turn and a waiting approval, as the preferences say, never for a throwaway thread", () => {
    const notices = new Notices();
    const done = { method: "turn/completed", params: { threadId: "t", turn: { id: "x", status: "completed" } } };
    expect(notices.take(done, () => ({ notifyTurns: "unfocused" }))).toEqual({ title: "Mightling", body: "A turn finished.", threadId: "t", always: false });
    expect(notices.take(done, () => ({ notifyTurns: "always" }))?.always).toBe(true);
    expect(notices.take(done, () => ({ notifyTurns: "never" }))).toBeNull();
    expect(notices.take({ method: "turn/completed", params: { threadId: "t", turn: { status: "failed" } } }, () => ({}))?.body).toBe("A turn stopped with an error.");
    // A commit message's thread: its answer says it is ephemeral, so its turn is not announced.
    notices.take({ id: "w1␟3", result: { thread: { id: "e1", ephemeral: true } } }, () => ({}));
    expect(notices.take({ method: "turn/completed", params: { threadId: "e1", turn: { status: "completed" } } }, () => ({}))).toBeNull();
    notices.take({ method: "thread/started", params: { thread: { id: "s1", parentThreadId: "t", ephemeral: false } } }, () => ({}));
    expect(notices.take({ method: "turn/completed", params: { threadId: "s1", turn: { status: "completed" } } }, () => ({}))).toBeNull();
    const approval = { id: "ask-1", method: "item/commandExecution/requestApproval", params: { threadId: "t" } };
    expect(notices.take(approval, () => ({}))).toEqual({ title: "Mightling", body: "The agent is waiting for an approval.", threadId: "t", always: false });
    expect(notices.take(approval, () => ({ notifyApprovals: false }))).toBeNull();
    expect(notices.take({ id: 2, method: "item/tool/requestUserInput", params: { threadId: "t" } }, () => ({}))?.body).toBe("The agent is waiting for a question.");
    // An MCP server's question (item 14) notifies like an approval; the vendor's identity check never shows.
    expect(notices.take({ id: 3, method: "mcpServer/elicitation/request", params: { threadId: "t", mode: "form" } }, () => ({}))?.body).toBe("The agent is waiting for an answer to an MCP server's question.");
    expect(notices.take({ id: 4, method: "mcpServer/elicitation/request", params: { threadId: "t", mode: "openai/userVerification" } }, () => ({}))).toBeNull();
    expect(notices.take({ method: "item/started", params: {} }, () => ({}))).toBeNull();
  });
});
