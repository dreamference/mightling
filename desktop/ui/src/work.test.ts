// The Work window against a scripted app-server stand-in (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md
// §12): a full turn with a command, a patch, an approval and an interrupt, as the server's
// messages arrive, and Full Access disabled at `on`. No window, no bridge, no model server.

import { describe, expect, it } from "vitest";

import { RpcClient, RpcError, type Transport } from "./rpc";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { ServerRequest } from "./protocol/ServerRequest";
import type { Thread } from "./protocol/v2/Thread";
import { contextUse, initialState, permissionChoices, projects, reduce, SERVER_EXITED, type Action, type WorkState } from "./store";

function thread(id: string, cwd: string, updatedAt: number, extra: Partial<Thread> = {}): Thread {
  return {
    id, cwd, updatedAt, createdAt: updatedAt, recencyAt: null, ephemeral: false, parentThreadId: null,
    preview: "", name: null, turns: [], status: { type: "idle" }, model: "m", modelProvider: "p",
    ...extra,
  } as unknown as Thread;
}

const note = (method: string, params: object): Action =>
  ({ type: "notification", notification: { method, params } as unknown as ServerNotification });

function run(actions: Action[], state: WorkState = initialState): WorkState {
  return actions.reduce(reduce, state);
}

/** A transport that records what the client sends. */
class Recorder implements Transport {
  sent: object[] = [];
  async send(message: object): Promise<void> {
    this.sent.push(message);
  }
}

describe("the JSON-RPC client", () => {
  it("matches answers to requests by id and routes the rest", async () => {
    const transport = new Recorder();
    const client = new RpcClient(transport);
    const notifications: string[] = [];
    const requests: string[] = [];
    client.onNotification = (n) => notifications.push(n.method);
    client.onServerRequest = (r) => requests.push(r.method);

    const started = client.request("thread/start", { cwd: "/w", model: "m" });
    const listed = client.request("thread/list", {});
    expect(transport.sent).toEqual([
      { id: 1, method: "thread/start", params: { cwd: "/w", model: "m" } },
      { id: 2, method: "thread/list", params: {} },
    ]);
    // Answers arrive out of order; each finds its request.
    client.receive({ id: 2, result: { data: [], nextCursor: null, backwardsCursor: null } });
    client.receive({ id: 1, error: { code: -32600, message: "invalid value for `sandbox_mode`" } });
    await expect(listed).resolves.toEqual({ data: [], nextCursor: null, backwardsCursor: null });
    await expect(started).rejects.toBeInstanceOf(RpcError);

    client.receive({ method: "turn/started", params: {} });
    client.receive({ id: 9, method: "item/fileChange/requestApproval", params: {} });
    client.receive({ id: 99, result: {} }); // an answer nobody waits for is ignored
    expect(notifications).toEqual(["turn/started"]);
    expect(requests).toEqual(["item/fileChange/requestApproval"]);

    await client.respond(9, { decision: "accept" });
    await client.respondError("x", -32601, "not handled here");
    expect(transport.sent.slice(2)).toEqual([
      { id: 9, result: { decision: "accept" } },
      { id: "x", error: { code: -32601, message: "not handled here" } },
    ]);
  });

  it("fails every waiting request when the server goes away", async () => {
    const client = new RpcClient(new Recorder());
    const pending = client.request("thread/read", { threadId: "t" });
    client.fail("the agent's server exited");
    await expect(pending).rejects.toThrow("the agent's server exited");
    expect(client.waitingCount).toBe(0);
  });
});

describe("a scripted session", () => {
  const opened = run([
    { type: "server", status: "ready" },
    { type: "opened", thread: thread("t1", "/home/u/project", 100) },
  ]);

  it("streams a turn with a command, its approval, a patch and the answer", () => {
    const approval = {
      id: 5, method: "item/commandExecution/requestApproval",
      params: { threadId: "t1", turnId: "u1", itemId: "c1", command: "pytest -q", kind: "command" },
    } as unknown as ServerRequest;
    const state = run([
      note("turn/started", { threadId: "t1", turn: { id: "u1", status: "inProgress", items: [], error: null } }),
      note("item/started", { threadId: "t1", turnId: "u1", item: { type: "userMessage", id: "m1", clientId: null, content: [{ type: "text", text: "fix it", text_elements: [] }] } }),
      note("item/started", { threadId: "t1", turnId: "u1", item: { type: "commandExecution", id: "c1", command: "pytest -q", aggregatedOutput: null, status: "inProgress", exitCode: null } }),
      { type: "serverRequest", request: approval },
    ], opened);
    expect(state.threads.t1.activeTurnId).toBe("u1");
    expect(state.requests).toHaveLength(1);
    expect(state.requests[0].threadId).toBe("t1");

    const done = run([
      note("serverRequest/resolved", { threadId: "t1", requestId: 5 }),
      note("item/commandExecution/outputDelta", { threadId: "t1", turnId: "u1", itemId: "c1", delta: "1 passed" }),
      note("item/commandExecution/outputDelta", { threadId: "t1", turnId: "u1", itemId: "c1", delta: " in 0.1s" }),
      note("item/completed", { threadId: "t1", turnId: "u1", item: { type: "fileChange", id: "f1", status: "completed", changes: [{ path: "a.py", kind: { type: "update", move_path: null }, diff: "@@ -1 +1 @@\n-x\n+y\n" }] } }),
      note("turn/diff/updated", { threadId: "t1", turnId: "u1", diff: "diff --git a/a.py b/a.py\n" }),
      note("item/started", { threadId: "t1", turnId: "u1", item: { type: "agentMessage", id: "a1", text: "", phase: null, memoryCitation: null, delivery: null, questions: null } }),
      note("item/agentMessage/delta", { threadId: "t1", turnId: "u1", itemId: "a1", delta: "Fixed " }),
      note("item/agentMessage/delta", { threadId: "t1", turnId: "u1", itemId: "a1", delta: "the test." }),
      note("thread/tokenUsage/updated", { threadId: "t1", turnId: "u1", tokenUsage: { total: { totalTokens: 9000 }, last: { totalTokens: 4500 }, modelContextWindow: 90000 } }),
      note("turn/completed", { threadId: "t1", turn: { id: "u1", status: "completed", items: [], error: null } }),
    ], state);
    const turn = done.threads.t1.turns[0];
    expect(done.requests).toHaveLength(0);
    expect(done.threads.t1.activeTurnId).toBeNull();
    expect(turn.status).toBe("completed");
    expect(turn.items.map((item) => item.type)).toEqual(["userMessage", "commandExecution", "fileChange", "agentMessage"]);
    const command = turn.items[1];
    expect(command.type === "commandExecution" && command.aggregatedOutput).toBe("1 passed in 0.1s");
    const answer = turn.items[3];
    expect(answer.type === "agentMessage" && answer.text).toBe("Fixed the test.");
    expect(turn.diff).toBe("diff --git a/a.py b/a.py\n");
    expect(contextUse(done.threads.t1.usage)).toEqual({ used: 4500, window: 90000, percent: 5 });
  });

  it("a completion that lists only the answer keeps every item the turn streamed, the answer updated in place", () => {
    const state = run([
      note("turn/started", { threadId: "t1", turn: { id: "u3", status: "inProgress", items: [], error: null } }),
      note("item/completed", { threadId: "t1", turnId: "u3", item: { type: "userMessage", id: "q3", content: [{ type: "text", text: "Fix it", text_elements: [] }] } }),
      note("item/completed", { threadId: "t1", turnId: "u3", item: { type: "commandExecution", id: "c3", command: "pytest", cwd: "/w", processId: null, status: "completed", commandActions: [], exitCode: 0, durationMs: 10, aggregatedOutput: "ok" } }),
      note("item/completed", { threadId: "t1", turnId: "u3", item: { type: "fileChange", id: "f3", status: "completed", changes: [] } }),
      note("item/started", { threadId: "t1", turnId: "u3", item: { type: "agentMessage", id: "a3", text: "", phase: null, memoryCitation: null, delivery: null, questions: null } }),
      note("item/agentMessage/delta", { threadId: "t1", turnId: "u3", itemId: "a3", delta: "Done" }),
      // As ling sends it: the completed turn carries the answer alone, in its final form.
      note("turn/completed", { threadId: "t1", turn: { id: "u3", status: "completed", error: null,
        items: [{ type: "agentMessage", id: "a3", text: "Done: the test is fixed.", phase: null, memoryCitation: null, delivery: null, questions: null }] } }),
    ], opened);
    const turn = state.threads.t1.turns[0];
    expect(turn.items.map((item) => item.id)).toEqual(["q3", "c3", "f3", "a3"]);
    const answer = turn.items[3];
    expect(answer.type === "agentMessage" && answer.text).toBe("Done: the test is fixed.");
  });

  it("a new thread is titled by its first question as it is sent, and a listing without one keeps it", () => {
    expect(opened.threads.t1.thread.preview).toBe("");
    const asked = run([
      note("turn/started", { threadId: "t1", turn: { id: "u4", status: "inProgress", items: [], error: null } }),
      note("item/started", { threadId: "t1", turnId: "u4", item: { type: "userMessage", id: "q4", clientId: null, content: [{ type: "image", url: "data:," }, { type: "text", text: "Create hello.sh", text_elements: [] }] } }),
    ], opened);
    expect(asked.threads.t1.thread.preview).toBe("Create hello.sh");
    // The second question does not rename it; ling's own name, when it sends one, wins in the title.
    const again = run([
      note("item/completed", { threadId: "t1", turnId: "u5", item: { type: "userMessage", id: "q5", clientId: null, content: [{ type: "text", text: "And run it", text_elements: [] }] } }),
      note("thread/name/updated", { threadId: "t1", threadName: "Hello script" }),
    ], asked);
    expect(again.threads.t1.thread.preview).toBe("Create hello.sh");
    expect(again.threads.t1.thread.name).toBe("Hello script");
    // A listing read before ling wrote the thread down has no preview yet: what the window knows stays.
    const listed = run([{ type: "threads", list: [thread("t1", "/w", 2)] }], again);
    expect(listed.threads.t1.thread.preview).toBe("Create hello.sh");
    expect(listed.threads.t1.thread.name).toBe("Hello script");
  });

  it("opening a thread keeps the time of its last activity, not the time the server resumed it", () => {
    const listed = run([{ type: "threads", list: [thread("t9", "/w", 1000)] }], opened);
    const turn = { id: "u", items: [], itemsView: "full", status: "completed", error: null, startedAt: 900, completedAt: 950, durationMs: 50000 };
    const resumed = run([{ type: "opened", thread: thread("t9", "/w", 5000, { turns: [turn] as unknown as Thread["turns"] }) }], listed);
    expect(resumed.threads.t9.thread.updatedAt).toBe(950);
    // No turns in the answer: the time the listing gave stays.
    const bare = run([{ type: "opened", thread: thread("t9", "/w", 5000) }], listed);
    expect(bare.threads.t9.thread.updatedAt).toBe(1000);
    // A thread the window never listed takes the server's word.
    expect(run([{ type: "opened", thread: thread("t8", "/w", 5000) }], opened).threads.t8.thread.updatedAt).toBe(5000);
  });

  it("an interrupted turn ends, and its streamed items stay", () => {
    const state = run([
      note("turn/started", { threadId: "t1", turn: { id: "u2", status: "inProgress", items: [], error: null } }),
      note("item/started", { threadId: "t1", turnId: "u2", item: { type: "reasoning", id: "r1", summary: [], content: [] } }),
      note("item/reasoning/summaryTextDelta", { threadId: "t1", turnId: "u2", itemId: "r1", delta: "Looking", summaryIndex: 0 }),
      note("item/reasoning/textDelta", { threadId: "t1", turnId: "u2", itemId: "r1", delta: "first", contentIndex: 1 }),
      note("turn/completed", { threadId: "t1", turn: { id: "u2", status: "interrupted", items: [], error: null } }),
    ], opened);
    const turn = state.threads.t1.turns[0];
    expect(turn.status).toBe("interrupted");
    expect(state.threads.t1.activeTurnId).toBeNull();
    const reasoning = turn.items[0];
    expect(reasoning.type === "reasoning" && [reasoning.summary, reasoning.content]).toEqual([["Looking"], ["", "first"]]);
  });

  it("a failed turn keeps its error, and a retry does not", () => {
    const failed = run([
      note("turn/started", { threadId: "t1", turn: { id: "u3", status: "inProgress", items: [], error: null } }),
      note("error", { threadId: "t1", turnId: "u3", willRetry: true, error: { message: "retrying" } }),
    ], opened);
    expect(failed.threads.t1.turns[0].error).toBeNull();
    const after = run([note("error", { threadId: "t1", turnId: "u3", willRetry: false, error: { message: "the model server stopped" } })], failed);
    expect(after.threads.t1.turns[0].error?.message).toBe("the model server stopped");
  });

  it("notifications for threads this window does not know change nothing", () => {
    const state = run([note("item/agentMessage/delta", { threadId: "other", turnId: "x", itemId: "y", delta: "z" })], opened);
    expect(state).toBe(opened);
  });

  it("a notice whose cause is gone is taken back, and the same notice does not stack up", () => {
    const told = run([
      { type: "notice", message: "Name the project folder for the new thread." },
      { type: "notice", message: "the model server stopped" },
      { type: "notice", message: "Name the project folder for the new thread." },
    ], opened);
    expect(told.notices).toEqual(["the model server stopped", "Name the project folder for the new thread."]);
    const named = run([{ type: "dropNotice", message: "Name the project folder for the new thread." }], told);
    expect(named.notices).toEqual(["the model server stopped"]);
    expect(run([{ type: "dropNotice", message: "Name the project folder for the new thread." }], named)).toBe(named);
  });

  it("a server that is ready again takes back the notices of its exit, and only those", () => {
    const exited = run([
      { type: "notice", message: "the model server stopped" },
      { type: "notice", message: `${SERVER_EXITED} with code 1` },
      { type: "server", status: "exited" },
      { type: "notice", message: SERVER_EXITED },
    ], opened);
    expect(run([{ type: "server", status: "starting" }], exited).notices).toEqual(exited.notices);
    expect(run([{ type: "server", status: "starting" }, { type: "server", status: "ready" }], exited).notices).toEqual(["the model server stopped"]);
  });

  it("the server exiting drops the questions it can no longer take answers to", () => {
    const asked = run([{ type: "serverRequest", request: { id: 1, method: "item/fileChange/requestApproval", params: { threadId: "t1" } } as unknown as ServerRequest }], opened);
    expect(run([{ type: "server", status: "exited" }], asked).requests).toEqual([]);
  });
});

describe("the sidebar", () => {
  it("groups threads by project, newest first, and hides subagents and ephemeral threads", () => {
    const state = run([{
      type: "threads",
      list: [
        thread("a", "/p/one", 10),
        thread("b", "/p/two", 30),
        thread("c", "/p/one", 20),
        thread("sub", "/p/one", 40, { parentThreadId: "a" }),
        thread("tmp", "/p/three", 50, { ephemeral: true }),
      ],
    }]);
    expect(projects(state).map((p) => [p.cwd, p.threads.map((t) => t.id)])).toEqual([
      ["/p/two", ["b"]],
      ["/p/one", ["c", "a"]],
    ]);
  });
});

describe("the permission picker", () => {
  const profiles = [
    { id: ":read-only", description: null, allowed: true },
    { id: ":workspace", description: null, allowed: true },
    { id: ":danger-full-access", description: null, allowed: true },
  ];

  it("disables Full Access at airgapped on, with the reason", () => {
    const at = (on: boolean) => permissionChoices(profiles, on).map((c) => [c.label, c.disabled]);
    expect(at(false)).toEqual([["Read only", false], ["Workspace", false], ["Full Access", false]]);
    expect(at(true)).toEqual([["Read only", false], ["Workspace", false], ["Full Access", true]]);
    expect(permissionChoices(profiles, true)[2].reason).toContain("airgapped is on");
  });

  it("keeps the server's own refusal", () => {
    const choices = permissionChoices([{ id: ":danger-full-access", description: null, allowed: false }], false);
    expect(choices[0].disabled).toBe(true);
  });
});

describe("the agent's markdown", async () => {
  const { renderMarkdown } = await import("./markdown");

  it("renders formatting but carries no HTML, script, navigation or remote load", () => {
    const html = renderMarkdown("**bold** and `code`\n\n<script>alert(1)</script>\n\n[site](https://example.com) ![img](https://example.com/x.png)");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<code>code</code>");
    expect(html).not.toContain("<script");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("<img");
    expect(html).toContain('title="https://example.com"');
  });

  it("makes a citation of the user's documents a link to open, and no other link", async () => {
    const { docLink } = await import("./markdown");
    const html = renderMarkdown("See [acme-msa.pdf, p.7](docs:documents/42#page=7) and [notes](docs:documents/../1) and [x](docs:/etc/passwd).");
    expect(html).toContain('<span class="link doc-link" role="link" tabindex="0" data-doc="documents/42" data-page="7" title="Open documents/42">acme-msa.pdf, p.7</span>');
    expect(html.match(/doc-link/g)?.length).toBe(1);
    expect(docLink("docs:design-notes/3")).toEqual({ doc: "design-notes/3", page: null });
    expect(docLink("docs:documents/3#page=x")).toBeNull();
  });

  it("shows the pictures the image_search tool stored, and no other image", () => {
    const html = renderMarkdown('![A "puffin"](/images/0123456789abcdef.jpg) ![x](/images/../secret.jpg) ![y](//example.com/0123456789abcdef.jpg)');
    expect(html).toContain('<img class="found-image" src="/images/0123456789abcdef.jpg" alt="A &quot;puffin&quot;"');
    expect(html.match(/<img/g)?.length).toBe(1);
    expect(html).toContain('<span class="image-alt">[x]</span>');
  });
});
