// Several windows on one app-server (DESKTOP_ELECTRON §13): request ids tagged per window and
// answered to the window that asked, everything else to all, one initialize per connection; one
// composer per thread. The routing cases (windows.vectors.json) run through WindowHub and then the
// policy, as the main process runs every message; and every one of `ling web`'s conformance cases
// holds from any window, as it holds for one.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { vetOutgoing, type PromptSource } from "./policy";
import { COMPOSER_METHODS, RequestRouter, WindowHub, type WindowKind } from "./windows";
import routing from "./windows.vectors.json";

const WEB = path.join(__dirname, "..", "..", "..", "ling-rs", "web");

type Step = {
  open?: string; kind?: WindowKind; close?: string; focus?: string; show?: string; thread?: string | null; take?: boolean; reset?: boolean;
  from?: string; message?: unknown; expect?: "send" | "reply" | "refused" | "nothing"; send?: unknown; reply?: unknown;
  server?: unknown; to?: string; payload?: unknown;
  owner?: string; is?: string | null; fallback?: string | null;
};

function stubs(airgappedOn: () => boolean = () => false): PromptSource {
  let made = 0;
  return {
    composed: (name) => `PROMPT:${name}`,
    newScratchFolder: () => `SCRATCH/${++made}`,
    scratchFolderOf: (thread) => (thread.startsWith("ask-") || thread.startsWith("chat-") ? `ASK/${thread}` : null),
    airgappedOn,
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
      cases: { name: string; message: unknown; accept: boolean; out?: Record<string, unknown>; pending?: string[]; pendingAfter?: string[]; airgapped?: "off" | "on" }[];
    };
    for (const kind of ["full", "popout", "pane"] as const) {
      const hub = new WindowHub();
      hub.open("1", "full");
      hub.open("2", kind);
      let on = false;
      const prompts = stubs(() => on);
      for (const testCase of vectors.cases) {
        on = testCase.airgapped === "on";
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

  it("sends notifications and the server's own requests to every window, and answers to them unchanged", () => {
    const router = new RequestRouter();
    expect(router.incoming({ method: "turn/started", params: {} }).to).toBe("all");
    expect(router.incoming({ id: "ask-1", method: "item/commandExecution/requestApproval", params: {} }).to).toBe("all");
    expect(router.outgoing("2", { id: "ask-1", result: { decision: "accept" } }).send).toEqual({ id: "ask-1", result: { decision: "accept" } });
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
});
