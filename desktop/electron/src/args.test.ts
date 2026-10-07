import { describe, expect, it } from "vitest";

import { auditSeconds, linkTarget, workTarget } from "./args";

const args = (text: string) => text.split(/\s+/).filter(Boolean);

describe("what ling app asks for", () => {
  it("opens Chat with no arguments, as before", () => {
    expect(workTarget([])).toBeNull();
    expect(workTarget(args("--chat"))).toBeNull();
    expect(workTarget(args("--ozone-platform=x11"))).toBeNull();
  });

  it("opens Work on a folder or a thread", () => {
    expect(workTarget(args("--work"))).toEqual({ cwd: null, thread: null });
    expect(workTarget(args("--cwd /home/u/project"))).toEqual({ cwd: "/home/u/project", thread: null });
    expect(workTarget(args("--work --thread 019a-thread"))).toEqual({ cwd: null, thread: "019a-thread" });
  });

  it("reads a mightling:// link", () => {
    expect(linkTarget("mightling://thread/019a-thread")).toEqual({ cwd: null, thread: "019a-thread" });
    expect(linkTarget("mightling://work?cwd=%2Fhome%2Fu%2Fp")).toEqual({ cwd: "/home/u/p", thread: null });
    // An id that is not an id is dropped (it is only ever sent to the server as a thread id, never joined to a path).
    expect(linkTarget("mightling://thread/not%20an%20id")).toEqual({ cwd: null, thread: null });
    expect(workTarget(["mightling://thread/t1"])).toEqual({ cwd: null, thread: "t1" });
  });

  it("knows the audit's session", () => {
    expect(auditSeconds({})).toBeNull();
    expect(auditSeconds({ MIGHTLING_APP_AUDIT: "x" })).toBeNull();
    expect(auditSeconds({ MIGHTLING_APP_AUDIT: "45" })).toBe(45);
  });
});
