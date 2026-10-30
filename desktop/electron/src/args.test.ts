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
    expect(linkTarget("ling://threads/new")).toEqual({ cwd: null, thread: null, prompt: null, page: "new" });
    expect(linkTarget("ling://new?prompt=Fix%20the%20build&path=%2Fhome%2Fu%2Fapp")).toEqual({ cwd: "/home/u/app", thread: null, prompt: "Fix the build", page: "new" });
    expect(linkTarget("ling://settings")).toEqual({ cwd: null, thread: null, page: "settings" });
    expect(linkTarget("ling://night")).toEqual({ cwd: null, thread: null, page: "night" });
    expect(linkTarget("ling://pets")).toEqual({ cwd: null, thread: null, page: "pets" });
    expect(linkTarget("ling://pets/install?url=https://example.com/p.zip")).toEqual({ cwd: null, thread: null, page: "pets" });
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

describe("Quick Entry's links (DESKTOP_PARITY_CLAUDE §2.9)", () => {
  it("reads ling://ask with a prompt for the composer, and ling://quick for the box", () => {
    expect(linkTarget("ling://ask?prompt=What%20is%20NVFP4%3F")).toEqual({ cwd: null, thread: null, page: "ask", prompt: "What is NVFP4?" });
    expect(linkTarget("ling://ask")).toEqual({ cwd: null, thread: null, page: "ask", prompt: null });
    expect(linkTarget(`ling://ask?prompt=${"x".repeat(5000)}`).prompt).toHaveLength(4000);
    expect(linkTarget("ling://quick")).toEqual({ cwd: null, thread: null, page: "quick" });
  });

  it("reads the Codex app's other links (DESKTOP §16 B19)", () => {
    expect(linkTarget("ling://threads/new?prompt=Fix%20it&path=%2Fw%2Fapp")).toEqual({ cwd: "/w/app", thread: null, prompt: "Fix it", page: "new" });
    expect(linkTarget("ling://new?originUrl=git%40github.com%3Aacme%2Fapp.git&prompt=x")).toEqual({ cwd: null, thread: null, prompt: "x", page: "new", origin: "git@github.com:acme/app.git" });
    expect(linkTarget("ling://settings/notifications")).toEqual({ cwd: null, thread: null, page: "settings", settingsPage: "notifications" });
    expect(linkTarget("ling://settings/Bad_Page")).toEqual({ cwd: null, thread: null, page: "settings" });
    expect(linkTarget("ling://automations")).toEqual({ cwd: null, thread: null, page: "night" });
    expect(linkTarget("ling://plugins/lint@team")).toEqual({ cwd: null, thread: null, page: "plugins", plugin: "lint@team" });
  });
});

describe("what ling app takes from its arguments, and how much", () => {
  it("reads a link only as an argument of its own, never inside another", () => {
    expect(workTarget(["--app-user-model-id=ling://threads/t1"])).toBeNull();
    expect(workTarget(["--opened-by", "https://example.com/?next=mightling://work?cwd=/etc"])).toBeNull();
  });

  it("caps what a link carries: a remote at 500 characters, a plugin's name at 200", () => {
    expect(linkTarget(`ling://new?originUrl=${"a".repeat(600)}`).origin).toBe("a".repeat(500));
    expect(linkTarget(`ling://plugins/${"p".repeat(300)}`).plugin).toBe("p".repeat(200));
  });

  it("runs the audit's session only for a finite time, with its preview on plain http alone", () => {
    expect(auditSeconds({ MIGHTLING_APP_AUDIT: "Infinity" })).toBeNull();
    expect(auditSeconds({ MIGHTLING_APP_AUDIT: "-5" })).toBeNull();
    expect(auditPreview({ MIGHTLING_APP_AUDIT_PREVIEW: "https://localhost/page" })).toBeNull();
    expect(auditPreview({ MIGHTLING_APP_AUDIT_PREVIEW: "http://localhost/page" })).toBe("http://localhost/page");
  });
});
