// The pull-request tab's `gh` (DESKTOP_PARITY Phase 3 item 9): what it is run with, and what is refused before it runs.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { branchName, ghEnv, handle, prNumber, review, type Gate, type Message } from "./prs";

describe("the pull-request tab's gh", () => {
  it("runs without prompts, pager or update check", () => {
    const env = ghEnv({ PATH: "/usr/bin" });
    expect(env).toMatchObject({ PATH: "/usr/bin", GH_PROMPT_DISABLED: "1", GH_NO_UPDATE_NOTIFIER: "1", GH_PAGER: "cat", NO_COLOR: "1" });
  });

  it("takes a pull request number and nothing else", () => {
    expect(prNumber(12)).toBe("12");
    expect(prNumber("7")).toBe("7");
    for (const bad of [0, -1, 1.5, "12; rm -rf /", "--web", null]) expect(() => prNumber(bad)).toThrow();
  });

  it("refuses an empty comment or change request, and verdicts it does not know, before running anything", async () => {
    await expect(review("/nowhere", 3, "comment", " ")).rejects.toThrow("needs words");
    await expect(review("/nowhere", 3, "merge" as never, "x")).rejects.toThrow("not a review verdict");
  });
});

// DESKTOP §16 B32: comments and reviews, and Create pull request, through a stand-in `gh` (MIGHTLING_GH)
// that records its arguments; a real `gh` never runs here.
// The stand-in is a script with a shebang, which Windows runs only through a shell; prs.ts runs gh.exe
// without one, as it should, so the two tests that reach the stand-in run on Linux and macOS.
const scriptOnly = process.platform === "win32";

describe("the tab's comments and reviews, and Create pull request", () => {
  let dir = "";
  let log = "";
  const saved = process.env.MIGHTLING_GH;
  beforeEach(() => {
    dir = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-prs-")));
    log = path.join(dir, "gh.jsonl");
    const gh = path.join(dir, "gh");
    fs.writeFileSync(gh, `#!/usr/bin/env node
const fs = require("fs");
const args = process.argv.slice(2);
fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify({ args, cwd: process.cwd() }) + "\\n");
if (args[0] === "auth") {
  if (process.env.GH_AUTH_FAIL === "1") { process.stderr.write("not logged in\\n"); process.exit(1); }
  process.exit(0);
}
if (args[1] === "list") console.log(JSON.stringify([{ number: 5, title: "Fix cache", state: "OPEN", isDraft: false, author: { login: "ada" }, headRefName: "fix", baseRefName: "main", reviewDecision: null, updatedAt: "2026-10-20T09:00:00Z", url: "https://github.example/acme/fetcher/pull/5", additions: 3, deletions: 1 }]));
else if (args[1] === "view" && args.includes("comments,reviews")) console.log(JSON.stringify({ comments: [{ author: { login: "ada" }, body: "Why 2 s?", createdAt: "2026-10-20T09:00:00Z" }], reviews: [] }));
else if (args[1] === "view") console.log(JSON.stringify({ number: 12, title: "Retry", state: "OPEN", isDraft: false, author: { login: "ada" }, headRefName: "retry", baseRefName: "main", reviewDecision: "APPROVED", updatedAt: "2026-10-20T09:00:00Z", url: "https://github.example/acme/fetcher/pull/12", additions: 10, deletions: 2, body: "Details" }));
else if (args[1] === "diff") console.log("diff --git a/a.ts b/a.ts\\n");
else if (args[1] === "review") process.exit(0);
else if (args[1] === "create") console.log("https://github.example/acme/fetcher/pull/16");
`);
    fs.chmodSync(gh, 0o755);
    process.env.MIGHTLING_GH = gh;
  });
  afterEach(() => {
    if (saved === undefined) delete process.env.MIGHTLING_GH;
    else process.env.MIGHTLING_GH = saved;
    fs.rmSync(dir, { recursive: true, force: true });
  });
  const ran = (): { args: string[]; cwd: string }[] => (fs.existsSync(log) ? fs.readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line)) : []);
  /** main.ts's gate: `offline` throws as main.ts's does when `ling airgapped --thread` answers on. */
  const gate = (level: "off" | "on", enabled = true): Gate & { asked: string[] } => {
    const asked: string[] = [];
    return {
      enabled, asked,
      offline: async (what) => {
        asked.push(what);
        if (level === "on") throw new Error(`airgapped is on (this session): ${what} would reach the network`);
      },
      checkRoot: async (root) => String(root),
    };
  };
  const activity: Message = { type: "pr/activity", root: "", number: 12, thread: "w1" };
  const create = (draft: Partial<{ title: string; body: string; base: string | null; draft: boolean }>): Message =>
    ({ type: "pr/create", root: dir, draft: { title: "", body: "", base: null, draft: false, ...draft }, thread: "w1" });

  it("refuses both at /airgapped on, before gh runs", async () => {
    const on = gate("on");
    await expect(handle({ ...activity, root: dir } as Message, on)).rejects.toThrow("airgapped is on (this session): the pull-request tab would reach the network");
    await expect(handle(create({ title: "Retry with backoff" }), on)).rejects.toThrow("airgapped is on (this session): creating a pull request would reach the network");
    expect(on.asked).toEqual(["the pull-request tab", "creating a pull request"]);
    expect(ran()).toEqual([]);
  });

  it("refuses both while the tab is off in Settings › Git, before asking anything", async () => {
    const off = gate("off", false);
    await expect(handle({ ...activity, root: dir } as Message, off)).rejects.toThrow("the pull-request tab is off");
    await expect(handle(create({ title: "x" }), off)).rejects.toThrow("the pull-request tab is off");
    expect(off.asked).toEqual([]);
    expect(ran()).toEqual([]);
  });

  it.skipIf(scriptOnly)("reads comments and reviews with gh pr view --json comments,reviews", async () => {
    expect(await handle({ ...activity, root: dir } as Message, gate("off"))).toEqual({ comments: [{ author: { login: "ada" }, body: "Why 2 s?", createdAt: "2026-10-20T09:00:00Z" }], reviews: [] });
    expect(ran()).toEqual([{ args: ["pr", "view", "12", "--json", "comments,reviews"], cwd: dir }]);
  });

  it.skipIf(scriptOnly)("creates the pull request with the user's title and description, each joined to its flag", async () => {
    const url = await handle(create({ title: "-Retry with backoff\nnow", body: "Waits 0.5 s, 1 s, 2 s.", base: "main", draft: true }), gate("off"));
    expect(url).toBe("https://github.example/acme/fetcher/pull/16");
    expect(ran()).toEqual([{ args: ["pr", "create", "--title=-Retry with backoff now", "--body=Waits 0.5 s, 1 s, 2 s.", "--base=main", "--draft"], cwd: dir }]);
    await handle(create({ title: "Log every retry" }), gate("off"));
    expect(ran()[1].args).toEqual(["pr", "create", "--title=Log every retry", "--body="]);
  });

  it("refuses an empty title or an odd base before gh runs", async () => {
    await expect(handle(create({ title: "  " }), gate("off"))).rejects.toThrow("needs a title");
    await expect(handle(create({ title: "x", base: "--web" }), gate("off"))).rejects.toThrow("not a branch name");
    expect(ran()).toEqual([]);
    expect(branchName("release/1.7")).toBe("release/1.7");
    for (const bad of ["-x", "a b", "a..b", "a;b", "main.lock", "", 3]) expect(() => branchName(bad)).toThrow();
  });

  it.skipIf(scriptOnly)("handles pr/list across all inboxes, pr/view, pr/diff, pr/review, auth status and missing gh", async () => {
    const off = gate("off");
    expect((await handle({ type: "pr/list", root: dir, inbox: "review", thread: "w1" }, off) as unknown[]).length).toBe(1);
    expect(await handle({ type: "pr/list", root: dir, inbox: "mine", thread: "w1" }, off)).toHaveLength(1);
    expect(await handle({ type: "pr/list", root: dir, inbox: "open", thread: "w1" }, off)).toHaveLength(1);
    await expect(handle({ type: "pr/list", root: dir, inbox: "closed" as never, thread: "w1" }, off)).rejects.toThrow("not an inbox");
    expect((await handle({ type: "pr/view", root: dir, number: 12, thread: "w1" }, off) as { body: string }).body).toBe("Details");
    expect(await handle({ type: "pr/diff", root: dir, number: 12, thread: "w1" }, off)).toContain("diff --git");
    expect(await handle({ type: "pr/review", root: dir, number: 12, verdict: "approve", body: "", thread: "w1" }, off)).toBe("Submitted.");
    expect(await handle({ type: "pr/review", root: dir, number: 12, verdict: "comment", body: "Looks good", thread: "w1" }, off)).toBe("Submitted.");

    const { status } = await import("./prs");
    expect(await status(dir)).toBe("Signed in.");
    process.env.GH_AUTH_FAIL = "1";
    try {
      expect(await status(dir)).toContain("not logged in");
    } finally {
      delete process.env.GH_AUTH_FAIL;
    }
    process.env.MIGHTLING_GH = path.join(dir, "no-such-gh");
    expect(await status(dir)).toContain("not installed");
  });
});
