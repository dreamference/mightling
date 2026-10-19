import { describe, expect, it } from "vitest";

import { FAILED, Reassembler, chunked, failure, isChunk } from "./api";

describe("large payloads cross in pieces", () => {
  it("leaves a small message whole and splits a large one into acknowledged pieces", () => {
    expect(chunked({ a: 1 }, 100)).toEqual([{ a: 1 }]);
    const big = { text: "x".repeat(250) };
    const pieces = chunked(big, 100);
    expect(pieces.length).toBe(3);
    expect(pieces.every(isChunk)).toBe(true);
    const back = new Reassembler();
    expect(back.take(pieces[2] as never)).toBeUndefined();
    expect(back.take(pieces[0] as never)).toBeUndefined();
    expect(back.take(pieces[1] as never)).toEqual(big);
  });
});

describe("a refusal crosses as an answer", () => {
  it("is told from every other answer, so the preload throws it and only it", () => {
    expect(failure({ [FAILED]: "the agent's server is not running" })).toBe("the agent's server is not running");
    for (const answer of [null, undefined, "text", 3, [], { result: 1 }, { [FAILED]: 1 }]) expect(failure(answer)).toBeNull();
  });
});
