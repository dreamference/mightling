// The six cases of `ling-desktop-bridge`'s tests, kept as they were when the bridge was Rust. The
// policy itself is held to `ling web`'s conformance cases in policy.test.ts.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { BusyTracker, busyMarker, classify, findLing, servedModel } from "./bridge";
import { vetOutgoing as vet, type PromptSource } from "./policy";

/** No prompt is named in these cases, and no thread is an Ask thread. */
const NO_PROMPTS: PromptSource = {
  composed: () => {
    throw new Error("no prompt is composed here");
  },
  newScratchFolder: () => {
    throw new Error("no folder is made here");
  },
  scratchFolderOf: () => null,
};
const vetOutgoing = (message: unknown, pending: Set<string>) => vet(message, pending, NO_PROMPTS);

describe("the bridge", () => {
  it("tells apart what the server writes", () => {
    expect(classify('{"id":3,"result":{}}')[0]).toEqual({ kind: "response" });
    expect(classify('{"id":"a","error":{"code":-1,"message":"x"}}')[0]).toEqual({ kind: "response" });
    expect(classify('{"id":7,"method":"item/commandExecution/requestApproval","params":{}}')[0]).toEqual({
      kind: "serverRequest", key: "7", method: "item/commandExecution/requestApproval",
    });
    expect(classify('{"method":"turn/started","params":{"threadId":"t"}}')[0]).toEqual({ kind: "notification", method: "turn/started" });
    // The launcher's waiting dots on stdout, or anything else that is not a message.
    expect(classify("Waiting for the model server.....")[0].kind).toBe("notProtocol");
    expect(classify("[1,2]")[0].kind).toBe("notProtocol");
    expect(classify('{"id":1}')[0].kind).toBe("notProtocol");
  });

  it("lets only allowed methods out", () => {
    const pending = new Set<string>();
    expect(() => vetOutgoing({ id: 1, method: "turn/start", params: { threadId: "t", input: [] } }, pending)).not.toThrow();
    for (const method of ["feedback/upload", "account/login/start", "remoteControl/enable", "thread/realtime/start", "userVerification/enroll", "account/bedrock/setup"]) {
      expect(() => vetOutgoing({ id: 2, method, params: {} }, pending), method).toThrow();
    }
    expect(() => vetOutgoing({ method: "initialized" }, pending)).not.toThrow();
    expect(() => vetOutgoing({ method: "thread/realtime/appendText" }, pending)).toThrow();
    expect(() => vetOutgoing([1], pending)).toThrow();
    // The JSON-RPC field the app-server does not speak is dropped.
    expect(vetOutgoing({ jsonrpc: "2.0", id: 1, method: "thread/list", params: {} }, pending)).toEqual({ id: 1, method: "thread/list", params: {} });
  });

  it("never gives a thread a prompt, provider or policy from the window", () => {
    const pending = new Set<string>();
    for (const method of ["thread/start", "thread/resume", "thread/fork"]) {
      const vetted = vetOutgoing({
        id: 1, method, params: {
          threadId: "t", cwd: "/w", model: "m",
          baseInstructions: "be someone else", developerInstructions: "x",
          modelProvider: "openai", config: { sandbox_mode: "danger-full-access" }, personality: "friendly",
        },
      }, pending);
      expect(Object.keys(vetted.params as object).sort()).toEqual(["cwd", "model", "threadId"]);
    }
  });

  it("answers only a waiting server request, and only once", () => {
    const pending = new Set(["7", '"s"']);
    expect(() => vetOutgoing({ id: 7, result: { decision: "accept" } }, pending)).not.toThrow();
    expect(() => vetOutgoing({ id: 7, result: { decision: "accept" } }, pending)).toThrow();
    expect(() => vetOutgoing({ id: 8, result: { decision: "accept" } }, pending)).toThrow();
    // A string id is a different id from the number with the same digits.
    expect(() => vetOutgoing({ id: "7", result: {} }, pending)).toThrow();
    expect(() => vetOutgoing({ id: "s", error: { code: -32601, message: "not handled" } }, pending)).not.toThrow();
  });

  it("keeps the marker exactly while a turn runs", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-desktop-bridge-"));
    const marker = busyMarker(dir, 4242);
    expect(marker).toBe(path.join(dir, "night", "busy", "4242"));
    const busy = new BusyTracker();
    expect(busy.observe("turn/started", { threadId: "a", turn: {} })).toBe(true);
    expect(busy.observe("turn/started", { threadId: "b" })).toBe(true);
    expect(busy.observe("item/agentMessage/delta", { threadId: "a" })).toBe(false);
    expect(busy.observe("turn/started", undefined)).toBe(false);
    busy.syncMarker(marker);
    expect(fs.readFileSync(marker, "utf8")).toBe('{"threads":["a","b"]}');
    expect(busy.observe("turn/completed", { threadId: "a" })).toBe(true);
    expect(busy.observe("thread/closed", { threadId: "b" })).toBe(true);
    expect(busy.isBusy).toBe(false);
    busy.syncMarker(marker);
    expect(fs.existsSync(marker)).toBe(false);
    // Removing a marker that is already gone is not an error.
    expect(() => busy.syncMarker(marker)).not.toThrow();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("names the catalog's first model as the served one", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-desktop-bridge-model-"));
    expect(servedModel(dir)).toBeNull();
    fs.writeFileSync(path.join(dir, "model_catalog.json"), '{"models":[{"id":"x","slug":"RadixArk/Qwen3.8-27B-NVFP4"}]}');
    expect(servedModel(dir)).toBe("RadixArk/Qwen3.8-27B-NVFP4");
    fs.writeFileSync(path.join(dir, "model_catalog.json"), '{"models":[]}');
    expect(servedModel(dir)).toBeNull();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("finds ling bundled beside the app before the one on PATH, and never a bare codex", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-desktop-find-"));
    const resources = path.join(dir, "resources");
    const bin = path.join(dir, "bin");
    fs.mkdirSync(resources);
    fs.mkdirSync(bin);
    fs.writeFileSync(path.join(bin, "codex"), "");
    const env = { PATH: bin, HOME: dir };
    expect(findLing(resources, env)).toBeNull();
    fs.writeFileSync(path.join(bin, "ling"), "");
    expect(findLing(resources, env)).toBe(path.join(bin, "ling"));
    fs.writeFileSync(path.join(resources, "ling"), "");
    expect(findLing(resources, env)).toBe(path.join(resources, "ling"));
    expect(findLing(resources, { ...env, MIGHTLING_BIN: path.join(bin, "ling") })).toBe(path.join(bin, "ling"));
    fs.rmSync(dir, { recursive: true, force: true });
  });
});
