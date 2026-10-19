import { describe, expect, it } from "vitest";

import { draftKeyOf, withDraft } from "./drafts";

describe("composer drafts", () => {
  it("are kept per thread, a new thread's apart, and sending clears only the one it came from", () => {
    const greet = draftKeyOf("work", "t-greet");
    const other = draftKeyOf("work", "t-other");
    const fresh = draftKeyOf("work", null);
    expect(new Set([greet, other, fresh, draftKeyOf("ask", null)]).size).toBe(4);
    let all = withDraft({}, greet, "Hello");
    all = withDraft(all, other, (text) => `${text}Other`);
    expect([all[greet], all[other], all[fresh]]).toEqual(["Hello", "Other", undefined]);
    // A new thread's first message: typed under the new key, cleared there when it is sent, even
    // though the thread it started is what the window shows by then.
    all = withDraft(all, fresh, "Greet with Hi");
    all = withDraft(all, fresh, "");
    expect(all[fresh]).toBeUndefined();
    expect(all[greet]).toBe("Hello");
    expect(withDraft(all, greet, "Hello")).toBe(all);
  });
});
