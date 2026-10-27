// The pet's blocked state (DESKTOP §16 B18).
import { describe, expect, it } from "vitest";

import { mood, moodText } from "./PetView";

const state = (extra: object) => ({ working: 0, waiting: 0, finished: 0, size: "medium" as const, image: null, ...extra });

describe("the pet's mood", () => {
  it("is blocked when a thread waits for an answer this app cannot give, after waiting", () => {
    expect(mood(state({ needs: [{ id: "b", title: "x", why: "blocked" }] }))).toBe("blocked");
    expect(mood(state({ waiting: 1, needs: [{ id: "b", title: "x", why: "blocked" }] }))).toBe("waiting");
    expect(moodText(state({ working: 1, needs: [{ id: "b", title: "x", why: "blocked" }] }))).toBe("One thread is blocked, one thread working.");
  });
});
