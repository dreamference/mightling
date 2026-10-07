import { describe, expect, it } from "vitest";

import { Reassembler, chunked, isChunk } from "./api";

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
