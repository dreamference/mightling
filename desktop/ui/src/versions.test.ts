// Earlier versions of an edited message (DESKTOP §16 B39): the lineage kept as a window preference.
import { describe, expect, it } from "vitest";

import { LINEAGES_MAX, VERSIONS_MAX, cleanLineage, editedTurn, recordVersion, stepVersion, versionsAt } from "./versions";

describe("the versions of an edited message", () => {
  it("has none until a message is edited, then shows each version in the thread it was sent in", () => {
    expect(versionsAt({}, "a", 1)).toBeNull();
    const one = recordVersion({}, "a", 1, "b");
    expect(one).toEqual({ "a:1": ["a", "b"] });
    expect(versionsAt(one, "a", 1)).toEqual({ at: 1, of: 2, threads: ["a", "b"] });
    expect(versionsAt(one, "b", 1)).toEqual({ at: 2, of: 2, threads: ["a", "b"] });
    // Other messages of either thread have one version.
    expect(versionsAt(one, "a", 0)).toBeNull();
    expect(versionsAt(one, "a", 2)).toBeNull();
    expect(versionsAt(one, "b", 2)).toBeNull();
  });

  it("joins an edit of a version to the message's versions: the third shows 3 / 3", () => {
    let lineage = recordVersion({}, "a", 1, "b");
    lineage = recordVersion(lineage, "b", 1, "c");
    expect(lineage).toEqual({ "a:1": ["a", "b", "c"] });
    lineage = recordVersion(lineage, "a", 1, "d");
    expect(versionsAt(lineage, "c", 1)).toEqual({ at: 3, of: 4, threads: ["a", "b", "c", "d"] });
    expect(stepVersion(versionsAt(lineage, "c", 1)!, -1)).toBe("b");
    expect(stepVersion(versionsAt(lineage, "c", 1)!, 1)).toBe("d");
    expect(stepVersion(versionsAt(lineage, "a", 1)!, -1)).toBeNull();
    expect(stepVersion(versionsAt(lineage, "d", 1)!, 1)).toBeNull();
    // Recording the same fork twice changes nothing.
    expect(recordVersion(lineage, "a", 1, "d")).toEqual(lineage);
  });

  it("reads a message before the edited one in a later version as the first version's", () => {
    // b is a version of a's message 2, so b's message 1 is a's: editing it there is a version of a's.
    let lineage = recordVersion({}, "a", 2, "b");
    lineage = recordVersion(lineage, "b", 1, "c");
    expect(lineage["a:1"]).toEqual(["a", "c"]);
    expect(versionsAt(lineage, "a", 1)).toMatchObject({ at: 1, of: 2 });
    expect(versionsAt(lineage, "b", 1)).toMatchObject({ at: 1, of: 2 });
    expect(versionsAt(lineage, "c", 1)).toMatchObject({ at: 2, of: 2 });
    // c's message 2 is its own (sent after the edit), and b's stays the second of a's message 2.
    expect(versionsAt(lineage, "c", 2)).toBeNull();
    expect(versionsAt(lineage, "b", 2)).toMatchObject({ at: 2, of: 2 });
  });

  it("keeps only well-formed entries from the stored preference", () => {
    expect(cleanLineage(null)).toEqual({});
    expect(cleanLineage([])).toEqual({});
    expect(cleanLineage({
      "a:1": ["a", "b"],
      "a": ["a", "b"],
      "c:x": ["c", "d"],
      "e:1": ["e"],
      "f:1": ["g", "f"],
      "h:1": ["h", "h"],
      "i:1": ["i", 3],
      "j:1": "j",
    })).toEqual({ "a:1": ["a", "b"] });
  });

  it("is bounded: the oldest messages and a message's middle versions go first, never the first version", () => {
    let lineage = {};
    for (let n = 0; n < LINEAGES_MAX + 5; n++) lineage = recordVersion(lineage, `t${n}`, 0, `f${n}`);
    expect(Object.keys(lineage)).toHaveLength(LINEAGES_MAX);
    expect(versionsAt(lineage, "t0", 0)).toBeNull();
    expect(versionsAt(lineage, `t${LINEAGES_MAX + 4}`, 0)).toMatchObject({ at: 1, of: 2 });
    let many = recordVersion({}, "a", 0, "v1");
    for (let n = 2; n <= VERSIONS_MAX + 3; n++) many = recordVersion(many, `v${n - 1}`, 0, `v${n}`);
    expect(many["a:0"]).toHaveLength(VERSIONS_MAX);
    expect(many["a:0"][0]).toBe("a");
    expect(many["a:0"].at(-1)).toBe(`v${VERSIONS_MAX + 3}`);
    // An entry used again moves to the newest end, so it outlives untouched ones.
    expect(Object.keys(recordVersion(recordVersion({}, "a", 0, "b"), "x", 0, "y")).at(-1)).toBe("x:0");
    expect(Object.keys(recordVersion(recordVersion(recordVersion({}, "a", 0, "b"), "x", 0, "y"), "b", 0, "c")).at(-1)).toBe("a:0");
  });

  it("makes Edit's new version of the last message, never while a turn runs", () => {
    const turn = (id: string, text: string | null) => ({ id, status: "completed", items: text === null ? [] : [{ type: "userMessage", id: `u${id}`, content: [{ type: "text", text, text_elements: [] }] }] });
    const view = (activeTurnId: string | null) => ({ thread: { id: "a" }, activeTurnId, usage: null, turns: [turn("t1", "first"), turn("t2", "second"), turn("t3", null)] }) as never;
    expect(editedTurn(view(null))?.id).toBe("t2");
    expect(editedTurn(view("t3"))).toBeNull();
    expect(editedTurn({ thread: { id: "a" }, activeTurnId: null, usage: null, turns: [] } as never)).toBeNull();
  });
});
