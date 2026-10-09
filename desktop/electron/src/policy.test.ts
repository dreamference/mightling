// The app's policy against `ling web`'s own conformance cases (ling-rs/web/vectors/outgoing.json),
// with the stubs every implementation uses: a prompt composes to "PROMPT:<name>", scratch folders
// are "SCRATCH/1", "SCRATCH/2", ... counted across the whole file, and a thread "ask-*" is an Ask
// thread whose folder is "ASK/<id>" (specs/DREAMFERENCE_MIGHTLING_ASK.md §2.3, §13).

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { POLICY, namedPrompt, parsePolicy, vetOutgoing, type PromptSource } from "./policy";

const WEB = path.join(__dirname, "..", "..", "..", "ling-rs", "web");

interface Case {
  name: string;
  message: unknown;
  accept: boolean;
  out?: unknown;
  pending?: string[];
  pendingAfter?: string[];
}

describe("the policy", () => {
  it("is the file ling web compiles in", () => {
    const file = JSON.parse(fs.readFileSync(path.join(WEB, "policy.json"), "utf8"));
    expect([...POLICY.allowedRequests].sort()).toEqual([...file.allowedRequests].sort());
    expect([...POLICY.droppedThreadFields]).toEqual(file.droppedThreadFields);
    expect([...POLICY.namedPrompts].sort()).toEqual([...file.namedPrompts].sort());
    expect(POLICY.namedPrompts.has("ask")).toBe(true);
    for (const refused of ["feedback/upload", "account/login/start", "remoteControl/enable", "thread/realtime/start"]) {
      expect(POLICY.allowedRequests.has(refused), refused).toBe(false);
    }
  });

  it("holds every conformance case", () => {
    const vectors = JSON.parse(fs.readFileSync(path.join(WEB, "vectors", "outgoing.json"), "utf8")) as { cases: Case[] };
    expect(vectors.cases.length).toBeGreaterThanOrEqual(25);
    let made = 0;
    const stub: PromptSource = {
      composed: (name) => `PROMPT:${name}`,
      newScratchFolder: () => `SCRATCH/${++made}`,
      scratchFolderOf: (thread) => (thread.startsWith("ask-") ? `ASK/${thread}` : null),
    };
    for (const testCase of vectors.cases) {
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

  it("names the prompt to compose before vetting, and only an allowed one other than default", () => {
    expect(namedPrompt({ id: 1, method: "thread/start", params: { prompt: "ask" } })).toBe("ask");
    expect(namedPrompt({ id: 1, method: "thread/start", params: { prompt: "default" } })).toBeNull();
    expect(namedPrompt({ id: 1, method: "thread/start", params: { prompt: "nonsense" } })).toBeNull();
    expect(namedPrompt({ id: 1, method: "thread/resume", params: { prompt: "ask" } })).toBeNull();
    expect(namedPrompt({ id: 1, method: "thread/start", params: {} })).toBeNull();
  });

  it("refuses a policy whose scratch prompt is not named", () => {
    const file = JSON.parse(fs.readFileSync(path.join(WEB, "policy.json"), "utf8"));
    expect(() => parsePolicy({ ...file, scratchPrompts: ["unnamed"] })).toThrow();
    expect(() => parsePolicy({ ...file, promptOpeners: ["turn/start"] })).toThrow();
  });
});
