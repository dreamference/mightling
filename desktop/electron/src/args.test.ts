import { describe, expect, it } from "vitest";

import { auditPreview, auditSeconds, linkTarget, workTarget } from "./args";

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

  it("reads the ling:// links of DESKTOP_PARITY §2.20", () => {
    expect(linkTarget("ling://threads/019a-thread")).toEqual({ cwd: null, thread: "019a-thread" });
    expect(linkTarget("ling://threads/new")).toEqual({ cwd: null, thread: null, page: "new" });
    expect(linkTarget("ling://new?prompt=Fix%20the%20build&path=%2Fhome%2Fu%2Fapp")).toEqual({ cwd: "/home/u/app", thread: null, prompt: "Fix the build", page: "new" });
    expect(linkTarget("ling://settings")).toEqual({ cwd: null, thread: null, page: "settings" });
    expect(linkTarget("ling://night")).toEqual({ cwd: null, thread: null, page: "night" });
    expect(linkTarget("ling://plugins/acme.lint")).toEqual({ cwd: null, thread: null, page: "plugins", plugin: "acme.lint" });
    expect(workTarget(["ling://threads/t2"])).toEqual({ cwd: null, thread: "t2" });
  });

  it("knows the audit's session", () => {
    expect(auditSeconds({})).toBeNull();
    expect(auditSeconds({ MIGHTLING_APP_AUDIT: "x" })).toBeNull();
    expect(auditSeconds({ MIGHTLING_APP_AUDIT: "45" })).toBe(45);
    expect(auditPreview({ MIGHTLING_APP_AUDIT_PREVIEW: "http://127.0.0.1:4100/page" })).toBe("http://127.0.0.1:4100/page");
    expect(auditPreview({ MIGHTLING_APP_AUDIT_PREVIEW: "https://example.com/" })).toBeNull();
    expect(auditPreview({ MIGHTLING_APP_AUDIT_PREVIEW: "http://192.168.1.2/" })).toBeNull();
    expect(auditPreview({})).toBeNull();
  });
});
