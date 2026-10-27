// Which of the server's messages become desktop notifications (DESKTOP_PARITY §2.1, §2.15; DESKTOP §16 B1).
import { describe, expect, it } from "vitest";

import { BODY_CAP, Notices, oneLine } from "./notices";

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
    expect(notices.take(done, () => ({ notifyTurns: "unfocused" }))).toEqual({ title: "Mightling", body: "The turn finished.", threadId: "t", always: false });
    expect(notices.take(done, () => ({ notifyTurns: "always" }))?.always).toBe(true);
    expect(notices.take(done, () => ({ notifyTurns: "never" }))).toBeNull();
    expect(notices.take({ method: "turn/completed", params: { threadId: "t", turn: { status: "failed" } } }, () => ({}))?.body).toBe("The turn stopped with an error.");
    // A commit message's thread: its answer says it is ephemeral, so its turn is not announced.
    notices.take({ id: "w1␟3", result: { thread: { id: "e1", ephemeral: true } } }, () => ({}));
    expect(notices.take({ method: "turn/completed", params: { threadId: "e1", turn: { status: "completed" } } }, () => ({}))).toBeNull();
    notices.take({ method: "thread/started", params: { thread: { id: "s1", parentThreadId: "t", ephemeral: false } } }, () => ({}));
    expect(notices.take({ method: "turn/completed", params: { threadId: "s1", turn: { status: "completed" } } }, () => ({}))).toBeNull();
    const approval = { id: "ask-1", method: "item/commandExecution/requestApproval", params: { threadId: "t" } };
    expect(notices.take(approval, () => ({}))).toEqual({ title: "Mightling", body: "The agent is waiting for an approval.", threadId: "t", always: false });
    expect(notices.take(approval, () => ({ notifyApprovals: false }))).toBeNull();
    expect(notices.take({ id: 2, method: "item/tool/requestUserInput", params: { threadId: "t" } }, () => ({}))?.body).toBe("The agent has a question.");
    // An MCP server's question (item 14) notifies like a question; the vendor's identity check never shows.
    expect(notices.take({ id: 3, method: "mcpServer/elicitation/request", params: { threadId: "t", mode: "form" } }, () => ({}))?.body).toBe("The agent is waiting for an answer to an MCP server's question.");
    expect(notices.take({ id: 4, method: "mcpServer/elicitation/request", params: { threadId: "t", mode: "openai/userVerification" } }, () => ({}))).toBeNull();
    expect(notices.take({ method: "item/started", params: {} }, () => ({}))).toBeNull();
  });

  it("name the thread and say what it ended with", () => {
    const notices = new Notices();
    // Names come from any answer that carries threads: a list, a read, a start.
    notices.take({ id: "w1␟1", result: { data: [{ id: "a", name: null, preview: "Fix the flaky\n  login test" }, { id: "b", name: "Release notes", preview: "x" }] } }, () => ({}));
    notices.take({ method: "turn/started", params: { threadId: "a", turn: { id: "t1" } } }, () => ({}));
    notices.take({ method: "item/completed", params: { threadId: "a", turnId: "t1", item: { type: "agentMessage", id: "m1", text: "Looking." } } }, () => ({}));
    notices.take({ method: "item/completed", params: { threadId: "a", turnId: "t1", item: { type: "agentMessage", id: "m2", text: "The test waited on a timer.\n\nIt now waits for the page." } } }, () => ({}));
    const done = notices.take({ method: "turn/completed", params: { threadId: "a", turn: { id: "t1", status: "completed" } } }, () => ({}));
    expect(done).toEqual({ title: "Fix the flaky login test", body: "The test waited on a timer. It now waits for the page.", threadId: "a", always: false });
    // The next turn starts fresh: no message, the plain words.
    notices.take({ method: "turn/started", params: { threadId: "a", turn: { id: "t2" } } }, () => ({}));
    expect(notices.take({ method: "turn/completed", params: { threadId: "a", turn: { id: "t2", status: "completed" } } }, () => ({}))?.body).toBe("The turn finished.");
    // A stopped turn says so, whatever it said before.
    notices.take({ method: "item/completed", params: { threadId: "b", item: { type: "agentMessage", id: "m3", text: "Half way." } } }, () => ({}));
    expect(notices.take({ method: "turn/completed", params: { threadId: "b", turn: { status: "interrupted" } } }, () => ({}))).toMatchObject({ title: "Release notes", body: "The turn was stopped." });
    // A new thread is named by its first message until it has a name.
    notices.take({ method: "thread/started", params: { thread: { id: "n1", name: null, preview: "", ephemeral: false, parentThreadId: null } } }, () => ({}));
    notices.take({ method: "item/completed", params: { threadId: "n1", item: { type: "userMessage", id: "u", content: [{ type: "text", text: "Shrink the hero image." }] } } }, () => ({}));
    expect(notices.take({ method: "turn/completed", params: { threadId: "n1", turn: { status: "completed" } } }, () => ({}))?.title).toBe("Shrink the hero image.");
    // A rename is followed.
    notices.take({ method: "thread/name/updated", params: { threadId: "b", threadName: "Notes for 1.8" } }, () => ({}));
    expect(notices.take({ id: 9, method: "item/fileChange/requestApproval", params: { threadId: "b", reason: "write the notes" } }, () => ({}))).toMatchObject({ title: "Notes for 1.8", body: "Change files? write the notes" });
  });

  it("say what waits: the command, the question, the server's message", () => {
    const notices = new Notices();
    notices.take({ method: "thread/started", params: { thread: { id: "t", name: "Build", preview: "" } } }, () => ({}));
    expect(notices.take({ id: 1, method: "item/commandExecution/requestApproval", params: { threadId: "t", command: "cargo test --release" } }, () => ({}))).toMatchObject({ title: "Build", body: "Run cargo test --release?" });
    expect(notices.take({ id: 2, method: "execCommandApproval", params: { threadId: "t", command: ["git", "push"] } }, () => ({}))?.body).toBe("Run git push?");
    expect(notices.take({ id: 3, method: "item/tool/requestUserInput", params: { threadId: "t", questions: [{ id: "q", header: "Scope", question: "Which crate first?", isOther: true, isSecret: false, options: null }] } }, () => ({}))?.body).toBe("Which crate first?");
    expect(notices.take({ id: 4, method: "mcpServer/elicitation/request", params: { threadId: "t", serverName: "tracker", mode: "form", message: "Pick a project" } }, () => ({}))?.body).toBe("tracker: Pick a project");
    const long = notices.take({ id: 5, method: "item/commandExecution/requestApproval", params: { threadId: "t", command: "x".repeat(400) } }, () => ({}));
    expect(long?.body.length).toBe(BODY_CAP);
    expect(long?.body.endsWith("…")).toBe(true);
  });

  it("have separate switches for approvals and questions, questions following approvals until set", () => {
    const notices = new Notices();
    const approval = { id: 1, method: "item/commandExecution/requestApproval", params: { threadId: "t" } };
    const question = { id: 2, method: "item/tool/requestUserInput", params: { threadId: "t", questions: [] } };
    expect(notices.take(question, () => ({ notifyApprovals: false }))).toBeNull();
    expect(notices.take(question, () => ({ notifyApprovals: false, notifyQuestions: null }))).toBeNull();
    expect(notices.take(question, () => ({ notifyApprovals: false, notifyQuestions: true }))).not.toBeNull();
    expect(notices.take(approval, () => ({ notifyApprovals: false, notifyQuestions: true }))).toBeNull();
    expect(notices.take(question, () => ({ notifyApprovals: true, notifyQuestions: false }))).toBeNull();
    expect(notices.take(approval, () => ({ notifyApprovals: true, notifyQuestions: false }))).not.toBeNull();
  });

  it("fold a message onto one line", () => {
    expect(oneLine("  a\n\tb  ", 10)).toBe("a b");
    expect(oneLine("abcdefghij", 5)).toBe("abcd…");
  });
});
