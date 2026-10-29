// A throwaway question for the window's own small jobs (turns.ts askOnce).
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RpcClient } from "./rpc";
import { askOnce, cleanMessage } from "./turns";

afterEach(() => {
  vi.useRealTimers();
});

describe("askOnce", () => {
  it("leaves no timer behind when the turn could not start", async () => {
    vi.useFakeTimers();
    let listening = 0;
    const client = {
      request: (method: string) => {
        if (method === "thread/start") return Promise.resolve({ thread: { id: "t1" } });
        if (method === "turn/start") return Promise.reject(new Error("refused"));
        return Promise.resolve({});
      },
    } as unknown as RpcClient;
    const listen = () => {
      listening++;
      return () => { listening--; };
    };
    await expect(askOnce(client, listen, { cwd: "/w", model: null, prompt: "hi", timeoutMs: 1000 })).rejects.toThrow("refused");
    expect(listening).toBe(0);
    // Its time-out would otherwise fire later and reject a promise nobody waits for.
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("a generated commit message", () => {
  it("loses the fence it was asked to leave out, whatever its language word", () => {
    expect(cleanMessage("```\nFix the parser\n```")).toBe("Fix the parser");
    expect(cleanMessage("```text\nFix the parser\n```")).toBe("Fix the parser");
    expect(cleanMessage("```git-commit\nFix the parser\n```")).toBe("Fix the parser");
    expect(cleanMessage("```commit_msg\nFix the parser\n```")).toBe("Fix the parser");
  });
});
