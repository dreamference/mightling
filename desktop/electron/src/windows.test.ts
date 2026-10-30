// Several windows on one app-server (DESKTOP_ELECTRON §13): request ids tagged per window and
// answered to the window that asked, everything else to all, one initialize per connection; one
// composer per thread. The routing cases (windows.vectors.json) run through WindowHub and then the
// policy, as the main process runs every message; and every one of `ling web`'s conformance cases
// holds from any window, as it holds for one.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { vetOutgoing, type PromptSource, type WorkspaceSandbox } from "./policy";
import { COMPOSER_METHODS, PANE_TEXT_MAX, RequestRouter, WindowHub, paneAction, type WindowKind } from "./windows";
import routing from "./windows.vectors.json";

const WEB = path.join(__dirname, "..", "..", "..", "ling-rs", "web");

type Step = {
  open?: string; kind?: WindowKind; close?: string; focus?: string; show?: string; thread?: string | null; take?: boolean; reset?: boolean;
  from?: string; message?: unknown; expect?: "send" | "reply" | "refused" | "nothing"; send?: unknown; reply?: unknown;
  server?: unknown; to?: string; payload?: unknown;
  owner?: string; is?: string | null; fallback?: string | null;
};

/** What a conformance case says about chat-repos checkouts and the caller (policy.test.ts reads the same fields). */
type RepoContext = {
  repoCheckouts: Map<string, boolean>;
  repoThreads: Map<string, string>;
  bridge: boolean;
  repoFullAccess?: Set<string>;
  workspace?: Partial<WorkspaceSandbox>;
};

function stubs(airgappedOn: () => boolean = () => false, repo: RepoContext = { repoCheckouts: new Map(), repoThreads: new Map(), bridge: false }): PromptSource {
  let made = 0;
  return {
    composed: (name) => `PROMPT:${name}`,
    newScratchFolder: () => `SCRATCH/${++made}`,
    scratchFolderOf: (thread) => (thread.startsWith("ask-") || thread.startsWith("chat-") ? `ASK/${thread}` : null),
    airgappedOn,
    repoCheckout: (cwd) => repo.repoCheckouts.get(cwd) ?? null,
    repoThreadCwd: (thread) => repo.repoThreads.get(thread) ?? null,
    repoThreadFullAccess: (thread) => repo.repoFullAccess?.has(thread) === true,
    isBridge: () => repo.bridge,
    workFolder: (cwd) => cwd.startsWith("WORK/"),
    workspaceSandbox: () => ({ networkAccess: false, writableRoots: [], excludeTmpdirEnvVar: false, excludeSlashTmp: false, ...repo.workspace }),
  };
}

/** One window's message the way main.ts and server.ts handle it: routed, then vetted. */
function fromWindow(hub: WindowHub, window: string, message: unknown, pending: Set<string>, prompts: PromptSource) {
  const routed = hub.outgoing(window, message);
  if (routed.send === null) return { send: null, reply: routed.reply };
  return { send: vetOutgoing(routed.send, pending, prompts), reply: routed.reply };
}

/** A message from the app-server the way server.ts and main.ts handle it. */
function fromServer(hub: WindowHub, message: Record<string, unknown>, pending: Set<string>) {
  if (typeof message.method === "string" && "id" in message) pending.add(JSON.stringify(message.id));
  const params = message.params as { requestId?: unknown } | undefined;
  if (message.method === "serverRequest/resolved" && params && "requestId" in params) pending.delete(JSON.stringify(params.requestId));
  return hub.incoming(message);
}

describe("the window routing cases", () => {
  for (const scenario of (routing as { scenarios: { name: string; steps: Step[] }[] }).scenarios) {
    it(scenario.name, () => {
      const hub = new WindowHub();
      const pending = new Set<string>();
      const prompts = stubs();
      scenario.steps.forEach((step, index) => {
        const where = `${scenario.name}, step ${index + 1}`;
        if (step.open !== undefined) hub.open(step.open, step.kind ?? "full");
        else if (step.close !== undefined) hub.close(step.close);
        else if (step.focus !== undefined) hub.focus(step.focus);
        else if (step.show !== undefined) hub.show(step.show, step.thread ?? null, step.take === true);
        else if (step.reset) hub.reset();
        else if (step.owner !== undefined) expect(hub.owner(step.owner), where).toBe(step.is ?? null);
        else if (step.fallback !== undefined) expect(hub.state("x").fallback, where).toBe(step.fallback);
        else if (step.server !== undefined) expect(fromServer(hub, step.server as Record<string, unknown>, pending), where).toEqual({ to: step.to, payload: step.payload });
        else if (step.from !== undefined) {
          if (step.expect === "refused") {
            expect(() => fromWindow(hub, step.from as string, step.message, pending, prompts), where).toThrow();
            return;
          }
          const out = fromWindow(hub, step.from, step.message, pending, prompts);
          if (step.expect === "send") expect(out, where).toEqual({ send: step.send, reply: null });
          else if (step.expect === "reply") expect(out, where).toEqual({ send: null, reply: step.reply });
          else expect(out, where).toEqual({ send: null, reply: null });
        } else throw new Error(`${where}: a step of no known kind`);
      });
    });
  }

  it("cover every kind of window and step", () => {
    const steps = (routing as { scenarios: { steps: Step[] }[] }).scenarios.flatMap((scenario) => scenario.steps);
    expect(new Set(steps.filter((step) => step.open).map((step) => step.kind))).toEqual(new Set(["full", "popout", "pane"]));
    expect(new Set(steps.filter((step) => step.from).map((step) => step.expect))).toEqual(new Set(["send", "reply", "refused", "nothing"]));
  });
});

describe("ling web's conformance cases from any window", () => {
  it("hold for a second window, a pop-out and a pane as for the first, ids tagged", () => {
    const vectors = JSON.parse(fs.readFileSync(path.join(WEB, "vectors", "outgoing.json"), "utf8")) as {
      cases: { name: string; message: unknown; accept: boolean; out?: Record<string, unknown>; pending?: string[]; pendingAfter?: string[]; airgapped?: "off" | "on"; repoCheckouts?: Record<string, boolean>; repoThreads?: Record<string, string>; repoFullAccess?: string[]; bridge?: boolean; workspaceSandbox?: Partial<WorkspaceSandbox> }[];
    };
    for (const kind of ["full", "popout", "pane"] as const) {
      const hub = new WindowHub();
      hub.open("1", "full");
      hub.open("2", kind);
      let on = false;
      const repo: RepoContext = { repoCheckouts: new Map(), repoThreads: new Map(), bridge: false };
      const prompts = stubs(() => on, repo);
      for (const testCase of vectors.cases) {
        on = testCase.airgapped === "on";
        repo.repoCheckouts = new Map(Object.entries(testCase.repoCheckouts ?? {}));
        repo.repoThreads = new Map(Object.entries(testCase.repoThreads ?? {}));
        repo.bridge = Boolean(testCase.bridge);
        repo.repoFullAccess = new Set(testCase.repoFullAccess ?? []);
        repo.workspace = testCase.workspaceSandbox;
        const pending = new Set(testCase.pending ?? []);
        const message = testCase.message as Record<string, unknown>;
        // A pane sends no composer method at all (refused before the policy is asked); the rest is the policy's call.
        const composer = typeof message?.method === "string" && COMPOSER_METHODS.has(message.method);
        if (!testCase.accept || (kind === "pane" && composer)) {
          expect(() => fromWindow(hub, "2", message, pending, prompts), `${kind}: ${testCase.name}`).toThrow();
          continue;
        }
        const out = fromWindow(hub, "2", message, pending, prompts);
        const expected = { ...testCase.out } as Record<string, unknown>;
        if (typeof message.method === "string" && "id" in message) expected.id = `2␟${JSON.stringify(message.id)}`;
        expect(out.send, `${kind}: ${testCase.name}`).toEqual(expected);
        if (testCase.pendingAfter) expect([...pending].sort(), testCase.name).toEqual([...testCase.pendingAfter].sort());
      }
    }
  });
});

describe("the request router", () => {
  it("tags each window's requests and gives the answers back to the window that asked", () => {
    const router = new RequestRouter();
    const first = router.outgoing("1", { id: 1, method: "initialize", params: {} });
    expect(first.reply).toBeNull();
    const sent = first.send as { id: string };
    expect(sent.id).not.toBe(1);
    expect(router.incoming({ id: sent.id, result: { userAgent: "ling" } })).toEqual({ to: "1", payload: { id: 1, result: { userAgent: "ling" } } });
    expect(router.outgoing("1", { method: "initialized" })).toEqual({ send: { method: "initialized" }, reply: null });

    // A later window's initialize is answered here; its initialized is not sent twice.
    expect(router.outgoing("2", { id: 1, method: "initialize", params: {} })).toEqual({ send: null, reply: { id: 1, result: { userAgent: "ling" } } });
    expect(router.outgoing("2", { method: "initialized" })).toEqual({ send: null, reply: null });

    // Both windows use id 7; each answer finds its own window.
    const a = router.outgoing("1", { id: 7, method: "thread/read", params: {} }).send as { id: string };
    const b = router.outgoing("2", { id: 7, method: "thread/read", params: {} }).send as { id: string };
    expect(a.id).not.toBe(b.id);
    expect(router.incoming({ id: b.id, result: { thread: "b" } })).toEqual({ to: "2", payload: { id: 7, result: { thread: "b" } } });
    expect(router.incoming({ id: a.id, error: { code: 1, message: "no" } })).toEqual({ to: "1", payload: { id: 7, error: { code: 1, message: "no" } } });
  });

  it("holds a window's initialize while the first is unanswered, and answers both with the server's one answer", () => {
    const router = new RequestRouter();
    // Two windows restored at launch both initialize before the server, still starting, answers.
    const first = router.outgoing("1", { id: 1, method: "initialize", params: {} }).send as { id: string };
    expect(router.outgoing("2", { id: 3, method: "initialize", params: {} })).toEqual({ send: null, reply: null });
    expect(router.incoming({ id: first.id, result: { userAgent: "ling" } })).toEqual({
      to: "1",
      payload: { id: 1, result: { userAgent: "ling" } },
      also: [{ to: "2", payload: { id: 3, result: { userAgent: "ling" } } }],
    });
    expect(router.outgoing("2", { method: "initialized" })).toEqual({ send: null, reply: null });

    // An initialize the server refused is refused to the windows held with it; the next one is sent again.
    const again = new RequestRouter();
    const sent = again.outgoing("1", { id: 1, method: "initialize", params: {} }).send as { id: string };
    again.outgoing("2", { id: 1, method: "initialize", params: {} });
    expect(again.incoming({ id: sent.id, error: { code: 1, message: "no" } }).also).toEqual([{ to: "2", payload: { id: 1, error: { code: 1, message: "no" } } }]);
    expect(again.outgoing("2", { id: 2, method: "initialize", params: {} }).send).toEqual({ id: "2␟2", method: "initialize", params: {} });
  });

  it("sends notifications and the server's own requests to every window, and answers to them unchanged", () => {
    const router = new RequestRouter();
    expect(router.incoming({ method: "turn/started", params: {} }).to).toBe("all");
    expect(router.incoming({ id: "ask-1", method: "item/commandExecution/requestApproval", params: {} }).to).toBe("all");
    expect(router.outgoing("2", { id: "ask-1", result: { decision: "accept" } }).send).toEqual({ id: "ask-1", result: { decision: "accept" } });
    // Notifications without an id and non-object values pass through untagged; untagged or malformed incoming ids broadcast to all.
    expect(router.outgoing("1", { method: "thread/unsubscribe", params: {} })).toEqual({ send: { method: "thread/unsubscribe", params: {} }, reply: null });
    expect(router.outgoing("1", "raw")).toEqual({ send: "raw", reply: null });
    expect(router.incoming({ id: 99, result: {} })).toEqual({ to: "all", payload: { id: 99, result: {} } });
    expect(router.incoming({ id: "1␟not-json", result: {} })).toEqual({ to: "all", payload: { id: "1␟not-json", result: {} } });
  });
});

describe("the window hub", () => {
  it("names the most recent window of a kind, and tells each window the composers' owners", () => {
    const hub = new WindowHub();
    hub.open("1", "full");
    hub.open("2", "popout");
    hub.open("3", "pane");
    hub.open("4", "full");
    expect(hub.mostRecent()).toBe("4");
    hub.focus("1");
    expect(hub.mostRecent()).toBe("1");
    hub.focus("3");
    expect(hub.mostRecent(["full", "popout"])).toBe("1");
    hub.focus("2");
    expect(hub.mostRecent(["full", "popout"])).toBe("2");
    expect(hub.mostRecent()).toBe("1");
    hub.show("4", "t1");
    hub.show("3", "t1");
    expect(hub.state("3")).toEqual({ self: "3", owners: { t1: "4" }, fallback: "2" });
    hub.close("4");
    expect(hub.state("1").owners).toEqual({});
    expect(() => hub.outgoing("4", { id: 1, method: "thread/list", params: {} })).toThrow();
  });

  it("tracks shown threads, window kinds, server-request ownership, and pane actions", () => {
    const hub = new WindowHub();
    hub.open("1", "full");
    hub.open("2", "popout");
    hub.open("3", "pane");
    expect(hub.kind("1")).toBe("full");
    expect(hub.kind("3")).toBe("pane");
    expect(hub.kind("missing")).toBeNull();
    expect(hub.shown("1")).toBeNull();
    expect(hub.shown("missing")).toBeNull();
    hub.focus("missing");
    hub.show("missing", "t1");
    hub.show("3", "t1");
    expect(hub.owner("t1")).toBeNull();

    // Window 1 shows t1 first, then Window 2 also shows t1 without taking; clearing Window 1 hands ownership to Window 2.
    hub.show("1", "t1");
    hub.show("2", "t1");
    expect(hub.shown("1")).toBe("t1");
    expect(hub.owner("t1")).toBe("1");
    hub.show("1", null);
    expect(hub.shown("1")).toBeNull();
    expect(hub.owner("t1")).toBe("2");

    // Server request with threadId: only the owner window (not a pane or non-owner) may answer.
    hub.incoming({ id: "srv-1", method: "item/commandExecution/requestApproval", params: { threadId: "t1" } });
    expect(() => hub.outgoing("1", { id: "srv-1", result: { decision: "accept" } })).toThrow("in another window");
    expect(hub.outgoing("2", { id: "srv-1", result: { decision: "accept" } }).send).toEqual({ id: "srv-1", result: { decision: "accept" } });

    // Server request resolved by notification clears tracking; a server request without threadId can be answered by a full window, not a pane.
    hub.incoming({ id: "srv-2", method: "item/tool/requestUserInput", params: { threadId: "t1" } });
    hub.incoming({ method: "serverRequest/resolved", params: { requestId: "srv-2" } });
    hub.incoming({ id: "srv-3", method: "account/chatgptAuthTokens/refresh", params: {} });
    expect(() => hub.outgoing("3", { id: 1, method: "turn/start", params: {} })).toThrow("a detached pane has no composer");
    expect(hub.outgoing("1", { id: "srv-3", result: {} }).send).toEqual({ id: "srv-3", result: {} });

    hub.reset();
    expect(hub.outgoing("2", { id: 1, method: "initialize", params: {} }).send).toEqual({ id: "2␟1", method: "initialize", params: {} });

    expect(paneAction({ kind: "newTerminal" }, "t1")).toEqual({ kind: "newTerminal", thread: "t1" });
    expect(paneAction({ kind: "compose", text: "fix this" }, "t1")).toEqual({ kind: "compose", thread: "t1", text: "fix this" });
    expect(paneAction({ kind: "openFile", path: "src/main.ts", line: 42 }, "t1")).toEqual({ kind: "openFile", thread: "t1", path: "src/main.ts", line: 42 });
    expect(paneAction({ kind: "openFile", path: "src/main.ts", line: 0 }, "t1")).toEqual({ kind: "openFile", thread: "t1", path: "src/main.ts", line: null });
    expect(() => paneAction(null, "t1")).toThrow("must be an object");
    expect(() => paneAction({ kind: "other" }, "t1")).toThrow();
    expect(() => paneAction({ kind: "compose", text: 123 }, "t1")).toThrow("text for the composer");
    expect(() => paneAction({ kind: "compose", text: "x".repeat(PANE_TEXT_MAX + 1) }, "t1")).toThrow("text for the composer");
    expect(() => paneAction({ kind: "openFile", path: "" }, "t1")).toThrow("must be named");
  });

  it("refuses every composer method from a window that does not own the thread, and from a pane", () => {
    const hub = new WindowHub();
    hub.open("1", "full");
    hub.open("2", "popout");
    hub.open("3", "pane");
    hub.show("1", "t1");
    hub.show("2", "t1");
    const composer = [
      "turn/start", "turn/steer", "turn/interrupt", "thread/shellCommand", "review/start", "thread/compact/start", "thread/revert",
      "thread/queue/add", "thread/queue/update", "thread/queue/delete", "thread/queue/reorder", "thread/queue/start",
      "thread/goal/set", "thread/goal/clear",
    ];
    expect([...COMPOSER_METHODS].sort()).toEqual([...composer].sort());
    for (const method of composer) {
      const message = { id: 7, method, params: { threadId: "t1" } };
      expect(() => hub.outgoing("2", message), method).toThrow("in another window");
      expect(() => hub.outgoing("3", message), method).toThrow("a detached pane has no composer");
      expect(hub.outgoing("1", message).send, method).toEqual({ ...message, id: "1␟7" });
    }
    // Reading, naming and archiving stay open to every window.
    expect(hub.outgoing("2", { id: 8, method: "thread/read", params: { threadId: "t1" } }).send).toEqual({ id: "2␟8", method: "thread/read", params: { threadId: "t1" } });
  });

  it("refuses an answer from a pane, and an error for an answer, outside the owner's window", () => {
    const hub = new WindowHub();
    hub.open("1", "full");
    hub.open("2", "full");
    hub.open("3", "pane");
    hub.show("1", "t1");
    // A thread no window owns: any window but a pane answers for it.
    hub.incoming({ id: "srv-1", method: "item/commandExecution/requestApproval", params: { threadId: "t9" } });
    expect(() => hub.outgoing("3", { id: "srv-1", result: { decision: "accept" } })).toThrow("in another window");
    expect(() => hub.outgoing("3", { id: "srv-1", error: { code: -1, message: "declined" } })).toThrow("in another window");
    expect(hub.outgoing("2", { id: "srv-1", result: { decision: "accept" } }).send).toEqual({ id: "srv-1", result: { decision: "accept" } });
    // An error is an answer too: refusing a request is the owner's alone.
    hub.incoming({ id: "srv-2", method: "item/fileChange/requestApproval", params: { threadId: "t1" } });
    expect(() => hub.outgoing("2", { id: "srv-2", error: { code: -1, message: "declined" } })).toThrow("in another window");
    expect(hub.outgoing("1", { id: "srv-2", error: { code: -1, message: "declined" } }).send).toEqual({ id: "srv-2", error: { code: -1, message: "declined" } });
  });

  it("forgets a server request once it is answered, resolved, or the server is gone", () => {
    const hub = new WindowHub();
    hub.open("1", "full");
    hub.open("2", "full");
    hub.show("1", "t1");
    const answer = (id: string) => ({ id, result: { decision: "accept" } });
    // Answered by its owner: a late answer from elsewhere is left to the server's policy, which no longer waits for it.
    hub.incoming({ id: "srv-1", method: "item/commandExecution/requestApproval", params: { threadId: "t1" } });
    hub.outgoing("1", answer("srv-1"));
    expect(hub.outgoing("2", answer("srv-1")).send).toEqual(answer("srv-1"));
    // Resolved by the server: the same.
    hub.incoming({ id: "srv-2", method: "item/commandExecution/requestApproval", params: { threadId: "t1" } });
    hub.incoming({ method: "serverRequest/resolved", params: { threadId: "t1", requestId: "srv-2" } });
    expect(hub.outgoing("2", answer("srv-2")).send).toEqual(answer("srv-2"));
    // A new server numbers its requests afresh: a request of the old one says nothing about the new one's.
    hub.incoming({ id: 0, method: "item/commandExecution/requestApproval", params: { threadId: "t1" } });
    hub.reset();
    hub.incoming({ id: 0, method: "account/chatgptAuthTokens/refresh", params: {} });
    expect(hub.outgoing("2", { id: 0, result: {} }).send).toEqual({ id: 0, result: {} });
  });
});
