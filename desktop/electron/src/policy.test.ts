// The app's policy against `ling web`'s own conformance cases (ling-rs/web/vectors/outgoing.json),
// with the stubs every implementation uses: a prompt composes to "PROMPT:<name>", scratch folders
// are "SCRATCH/1", "SCRATCH/2", ... counted across the whole file, and a thread "ask-*" is an Ask
// thread whose folder is "ASK/<id>" (specs/DREAMFERENCE_MIGHTLING_ASK.md §2.3, §13).

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { POLICY, airgapThread, namedPrompt, parsePolicy, threadPlace, vetOutgoing, type PromptSource } from "./policy";

const WEB = path.join(__dirname, "..", "..", "..", "ling-rs", "web");

interface Case {
  name: string;
  message: unknown;
  accept: boolean;
  out?: unknown;
  pending?: string[];
  pendingAfter?: string[];
  airgapped?: "off" | "on";
  repoCheckouts?: Record<string, boolean>;
  repoThreads?: Record<string, string>;
  repoFullAccess?: string[];
  bridge?: boolean;
}

describe("the policy", () => {
  it("is the file ling web compiles in", () => {
    const file = JSON.parse(fs.readFileSync(path.join(WEB, "policy.json"), "utf8"));
    expect([...POLICY.allowedRequests].sort()).toEqual([...file.allowedRequests].sort());
    expect([...POLICY.droppedThreadFields]).toEqual(file.droppedThreadFields);
    expect([...POLICY.namedPrompts].sort()).toEqual([...file.namedPrompts].sort());
    expect(POLICY.namedPrompts.has("ask")).toBe(true);
    expect(POLICY.refusedImportItems.has("PLUGINS"), "plugins import while airgapped is off").toBe(false);
    expect(POLICY.airgappedRefusedImportItems.has("PLUGINS"), "an import fetches no plugin at on").toBe(true);
    for (const fetches of ["plugin/install", "marketplace/add", "marketplace/upgrade"]) {
      expect(POLICY.allowedRequests.has(fetches) && POLICY.airgappedRefusedRequests.has(fetches), fetches).toBe(true);
    }
    expect(POLICY.airgappedThreadConfig["features.plugins"]).toBe(false);
    // Deleting a thread for good is local: allowed at both levels (DESKTOP §16 B36).
    expect(POLICY.allowedRequests.has("thread/delete") && !POLICY.airgappedRefusedRequests.has("thread/delete")).toBe(true);
    for (const refused of ["feedback/upload", "account/login/start", "remoteControl/enable", "thread/realtime/start"]) {
      expect(POLICY.allowedRequests.has(refused), refused).toBe(false);
    }
  });

  it("holds every conformance case", () => {
    const vectors = JSON.parse(fs.readFileSync(path.join(WEB, "vectors", "outgoing.json"), "utf8")) as { cases: Case[] };
    expect(vectors.cases.length).toBeGreaterThanOrEqual(25);
    let made = 0;
    let on = false;
    let repoCheckouts = new Map<string, boolean>();
    let repoThreads = new Map<string, string>();
    let repoFullAccess = new Set<string>();
    let bridge = false;
    const stub: PromptSource = {
      composed: (name) => `PROMPT:${name}`,
      newScratchFolder: () => `SCRATCH/${++made}`,
      scratchFolderOf: (thread) => (thread.startsWith("ask-") || thread.startsWith("chat-") ? `ASK/${thread}` : null),
      airgappedOn: () => on,
      repoCheckout: (cwd) => repoCheckouts.get(cwd) ?? null,
      repoThreadCwd: (threadId) => repoThreads.get(threadId) ?? null,
      repoThreadFullAccess: (threadId) => repoFullAccess.has(threadId),
      isBridge: () => bridge,
      workFolder: (cwd) => cwd.startsWith("WORK/"),
    };
    for (const testCase of vectors.cases) {
      expect(["off", "on"], testCase.name).toContain(testCase.airgapped ?? "off");
      on = testCase.airgapped === "on";
      repoCheckouts = new Map(Object.entries(testCase.repoCheckouts ?? {}));
      repoThreads = new Map(Object.entries(testCase.repoThreads ?? {}));
      repoFullAccess = new Set(testCase.repoFullAccess ?? []);
      bridge = Boolean(testCase.bridge);
      const pending = new Set(testCase.pending ?? []);
      if (testCase.accept) {
        let out: unknown;
        expect(() => (out = vetOutgoing(testCase.message, pending, stub)), testCase.name).not.toThrow();
        expect(out, testCase.name).toEqual(testCase.out);
      } else {
        expect(() => vetOutgoing(testCase.message, pending, stub), testCase.name).toThrow();
      }
      if (testCase.pendingAfter) expect([...pending].sort(), testCase.name).toEqual([...testCase.pendingAfter].sort());
    }
  });

  it("lets only an Ask thread's fork leave its folder, only for a Work folder (DESKTOP §16 B33)", () => {
    expect(POLICY.forkRequest).toBe("thread/fork");
    const source = (workFolder?: (cwd: string) => boolean): PromptSource => ({
      composed: (name) => name,
      newScratchFolder: () => "SCRATCH/1",
      scratchFolderOf: (thread) => (thread.startsWith("ask-") || thread.startsWith("chat-") ? `ASK/${thread}` : null),
      airgappedOn: () => false,
      repoCheckout: () => null,
      repoThreadCwd: () => null,
      repoThreadFullAccess: () => false,
      isBridge: () => false,
      ...(workFolder ? { workFolder } : {}),
    });
    const vet = (method: string, thread: string, cwd: string, prompts = source((folder) => folder.startsWith("WORK/"))) =>
      (vetOutgoing({ id: 1, method, params: { threadId: thread, cwd, sandbox: "danger-full-access", permissions: ":danger-full-access", runtimeWorkspaceRoots: ["/"] } }, new Set(), prompts).params as Record<string, unknown>);
    expect(vet("thread/fork", "ask-7", "WORK/repo")).toEqual({ threadId: "ask-7", cwd: "WORK/repo", sandbox: "workspace-write" });
    expect(vet("thread/fork", "ask-7", "/home/user").cwd).toBe("ASK/ask-7");
    expect(vet("thread/resume", "ask-7", "WORK/repo").cwd).toBe("ASK/ask-7");
    // A phone's thread continued in a project leaves its prompt's config behind.
    expect(vet("thread/fork", "chat-3", "WORK/repo").config).toBeUndefined();
    expect((vet("thread/fork", "chat-3", "/home/user").config as Record<string, unknown>).approval_policy).toBe("never");
    // A Work thread's fork is not the policy's to place: as asked, as before.
    expect(vet("thread/fork", "w1", "WORK/repo").sandbox).toBe("danger-full-access");
    // A source that cannot tell a Work folder keeps every Ask fork in its folder.
    expect(vet("thread/fork", "ask-7", "WORK/repo", source()).cwd).toBe("ASK/ask-7");
    expect(vet("thread/fork", "ask-7", "WORK/repo", source(() => false)).cwd).toBe("ASK/ask-7");
  });

  it("names the prompt to compose before vetting, and only an allowed one other than default", () => {
    expect(namedPrompt({ id: 1, method: "thread/start", params: { prompt: "ask" } })).toBe("ask");
    expect(namedPrompt({ id: 1, method: "thread/start", params: { prompt: "default" } })).toBeNull();
    expect(namedPrompt({ id: 1, method: "thread/start", params: { prompt: "nonsense" } })).toBeNull();
    expect(namedPrompt({ id: 1, method: "thread/resume", params: { prompt: "ask" } })).toBeNull();
    expect(namedPrompt({ id: 1, method: "thread/start", params: {} })).toBeNull();
  });

  it("judges a request refused at on by the thread it names, and gives the ! command its own reason", () => {
    expect(airgapThread({ id: 1, method: "thread/shellCommand", params: { threadId: "t1", command: "ls" } })).toBe("t1");
    expect(airgapThread({ id: 1, method: "plugin/install", params: { pluginName: "x" } })).toBeNull();
    expect(airgapThread({ id: 1, method: "turn/start", params: { threadId: "t1" } })).toBeNull();
    const asked: (string | null)[] = [];
    const sealed: PromptSource = {
      composed: (name) => name,
      newScratchFolder: () => "SCRATCH",
      scratchFolderOf: () => null,
      airgappedOn: (thread) => (asked.push(thread), thread === "sealed"),
      repoCheckout: () => null,
      repoThreadCwd: () => null,
      repoThreadFullAccess: () => false,
      isBridge: () => false,
    };
    const shell = (thread: string) => ({ id: 1, method: "thread/shellCommand", params: { threadId: thread, command: "ls" } });
    expect(() => vetOutgoing(shell("open"), new Set(), sealed)).not.toThrow();
    expect(() => vetOutgoing(shell("sealed"), new Set(), sealed)).toThrow(/airgapped is on, and a ! command/);
    expect(asked).toEqual(["open", "sealed"]);
  });

  it("reads a thread and its folder from what the server says", () => {
    expect(threadPlace({ method: "thread/started", params: { thread: { id: "t1", cwd: "/work/repo" } } })).toEqual({ thread: "t1", cwd: "/work/repo" });
    expect(threadPlace({ id: 3, result: { thread: { id: "t2", cwd: "/work/other" } } })).toEqual({ thread: "t2", cwd: "/work/other" });
    expect(threadPlace({ id: 4, result: {} })).toBeNull();
  });

  it("refuses a reason for a request not refused at on", () => {
    const file = JSON.parse(fs.readFileSync(path.join(WEB, "policy.json"), "utf8"));
    expect(() => parsePolicy({ ...file, airgappedRefusalReasons: { "thread/list": "no" } })).toThrow();
    expect(() => parsePolicy({ ...file, airgappedRefusalReasons: { "thread/shellCommand": 1 } })).toThrow();
  });

  it("refuses a policy whose scratch prompt is not named", () => {
    const file = JSON.parse(fs.readFileSync(path.join(WEB, "policy.json"), "utf8"));
    expect(() => parsePolicy({ ...file, scratchPrompts: ["unnamed"] })).toThrow();
    expect(() => parsePolicy({ ...file, promptOpeners: ["turn/start"] })).toThrow();
  });
});
