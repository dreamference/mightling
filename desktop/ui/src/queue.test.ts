// Fork from here and queue edits (DESKTOP_PARITY §4.5 item 22).
import { describe, expect, it } from "vitest";

import { editedInput, forkAt, movedQueue } from "./turns";
import type { RpcClient } from "./rpc";
import type { UserInput } from "./protocol/v2/UserInput";

const text = (value: string): UserInput => ({ type: "text", text: value, text_elements: [] });

describe("a queued message", () => {
  it("takes new words in its text's place and keeps its image and skill", () => {
    const image: UserInput = { type: "localImage", path: "/x/a.png" };
    const skill = { type: "skill", name: "night-shift", path: "/s/SKILL.md" } as unknown as UserInput;
    expect(editedInput([text("old"), image, skill], "new")).toEqual([text("new"), image, skill]);
    expect(editedInput([image, text("a"), text("b")], "new")).toEqual([image, text("new")]);
    expect(editedInput([image], "words")).toEqual([text("words"), image]);
    expect(editedInput([text("old")], "  ")).toEqual([]);
  });

  it("moves up or down within the queue, never past its ends", () => {
    expect(movedQueue(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(movedQueue(["a", "b", "c"], "b", 1)).toEqual(["a", "c", "b"]);
    expect(movedQueue(["a", "b"], "a", -1)).toBeNull();
    expect(movedQueue(["a", "b"], "b", 1)).toBeNull();
    expect(movedQueue(["a"], "x", 1)).toBeNull();
  });
});

describe("a fork from an earlier turn", () => {
  it("asks through the turn, or before it", async () => {
    const sent: unknown[] = [];
    const client = { request: async (method: string, params: unknown) => { sent.push({ method, params }); return { thread: { id: "f" } }; } } as unknown as RpcClient;
    await forkAt(client, "t", "turn-1", "through", "m");
    await forkAt(client, "t", "turn-2", "before", null);
    expect(sent).toEqual([
      { method: "thread/fork", params: { threadId: "t", lastTurnId: "turn-1", model: "m" } },
      { method: "thread/fork", params: { threadId: "t", beforeTurnId: "turn-2", model: null } },
    ]);
  });
});
