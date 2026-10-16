// Managed worktrees, hand-off and local environments against scratch repositories (DESKTOP_PARITY
// §2.6): the codex-worktree contract (bucket, detached, core.worktree, owner record), the source's
// changes and `.worktreeinclude`, snapshot before removal and Restore, Create branch here, hand-off
// both ways, environments read from .codex/ and .mightling/ and written to .mightling/ only, and a
// task's output. Each repository lives in a temporary folder with no global git configuration.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { WRITE_FILE, environments, readEnvironment, writeEnvironment } from "./environments";
import { gitEnv } from "./git";
import { Tasks, type TaskEvent } from "./tasks";
import * as worktree from "./worktree";

const scratchHome = fs.mkdtempSync(path.join(os.tmpdir(), "ling-wt-home-"));
const saved = { HOME: process.env.HOME, GIT_CONFIG_GLOBAL: process.env.GIT_CONFIG_GLOBAL, GIT_CONFIG_NOSYSTEM: process.env.GIT_CONFIG_NOSYSTEM };

beforeAll(() => {
  process.env.HOME = scratchHome;
  process.env.GIT_CONFIG_GLOBAL = path.join(scratchHome, "gitconfig");
  process.env.GIT_CONFIG_NOSYSTEM = "1";
  fs.writeFileSync(process.env.GIT_CONFIG_GLOBAL, "[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n");
});
afterAll(() => {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

const run = (cwd: string, ...args: string[]) => execFileSync("git", args, { cwd, encoding: "utf8", env: gitEnv() }).trim();

/** A repository `app` with a committed file, an ignored .env, a .worktreeinclude naming it, and a subfolder. */
function repo(): { source: string; root: string } {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "ling-wt-")));
  const source = path.join(base, "app");
  fs.mkdirSync(path.join(source, "src"), { recursive: true });
  run(source, "init", "-q");
  fs.writeFileSync(path.join(source, "src", "main.py"), "print('hello')\n");
  fs.writeFileSync(path.join(source, ".gitignore"), ".env\nbuild/\nAGENTS.override.md\n");
  fs.writeFileSync(path.join(source, ".worktreeinclude"), ".env\n");
  run(source, "add", "-A");
  run(source, "commit", "-q", "-m", "first");
  fs.writeFileSync(path.join(source, ".env"), "TOKEN=local\n");
  fs.mkdirSync(path.join(source, "build"));
  fs.writeFileSync(path.join(source, "build", "out.bin"), "x");
  fs.writeFileSync(path.join(source, "AGENTS.override.md"), "Local rules.\n");
  return { source, root: path.join(base, "home", ".mightling", "worktrees") };
}

describe("managed worktrees", () => {
  it("follow the codex-worktree contract and copy only what .worktreeinclude names", async () => {
    const { source, root } = repo();
    const created = await worktree.create({ sourceCwd: path.join(source, "src"), base: null, withChanges: false, worktreeRoot: root });
    expect(worktree.isManaged(root, created.root)).toBe(true);
    expect(path.basename(created.root)).toBe("app");
    expect(created.cwd).toBe(path.join(created.root, "src"));
    expect(created.sourceRoot).toBe(source);
    expect(run(created.root, "rev-parse", "HEAD")).toBe(run(source, "rev-parse", "HEAD"));
    expect(() => run(created.root, "symbolic-ref", "-q", "HEAD")).toThrow();
    const configWorktree = run(created.root, "rev-parse", "--path-format=absolute", "--git-path", "config.worktree");
    expect(run(created.root, "config", "--file", configWorktree, "core.worktree")).toBe(created.root);
    expect(fs.readFileSync(path.join(created.root, ".env"), "utf8")).toBe("TOKEN=local\n");
    expect(fs.existsSync(path.join(created.root, "AGENTS.override.md"))).toBe(true);
    expect(fs.existsSync(path.join(created.root, "build"))).toBe(false);
    expect(created.included).toEqual([".env", "AGENTS.override.md"]);

    await worktree.bindThread(created.root, "thread-1");
    await worktree.bindThread(created.root, "thread-1");
    await expect(worktree.bindThread(created.root, "thread-2")).rejects.toThrow("already belongs to thread thread-1");
    const ownerFile = path.resolve(created.root, run(created.root, "rev-parse", "--git-path", "codex-thread.json"));
    expect(JSON.parse(fs.readFileSync(ownerFile, "utf8"))).toEqual({ version: 1, ownerThreadId: "thread-1" });
    expect((await worktree.list(source, root)).map((entry) => [entry.root, entry.owner])).toEqual([[created.root, "thread-1"]]);
  });

  it("start from a branch, or from the current HEAD with the uncommitted changes applied", async () => {
    const { source, root } = repo();
    run(source, "checkout", "-q", "-b", "feature");
    fs.writeFileSync(path.join(source, "src", "main.py"), "print('feature')\n");
    run(source, "commit", "-q", "-am", "feature");
    fs.writeFileSync(path.join(source, "src", "main.py"), "print('work in progress')\n");
    fs.writeFileSync(path.join(source, "src", "new.py"), "x = 1\n");
    const fromMain = await worktree.create({ sourceCwd: source, base: "main", withChanges: false, worktreeRoot: root });
    expect(fs.readFileSync(path.join(fromMain.root, "src", "main.py"), "utf8")).toBe("print('hello')\n");
    const withChanges = await worktree.create({ sourceCwd: source, base: null, withChanges: true, worktreeRoot: root });
    expect(fs.readFileSync(path.join(withChanges.root, "src", "main.py"), "utf8")).toBe("print('work in progress')\n");
    expect(fs.readFileSync(path.join(withChanges.root, "src", "new.py"), "utf8")).toBe("x = 1\n");
    // The source is left as it was.
    expect(run(source, "status", "--porcelain")).toContain("src/new.py");
    await expect(worktree.create({ sourceCwd: source, base: "--upload-pack=x", withChanges: false, worktreeRoot: root })).resolves.toBeTruthy();
    await expect(worktree.create({ sourceCwd: source, base: null, withChanges: false, worktreeRoot: "relative" })).rejects.toThrow("absolute");
  });

  it("are snapshotted before removal, and Restore brings the work back in a new one", async () => {
    const { source, root } = repo();
    const created = await worktree.create({ sourceCwd: source, base: null, withChanges: false, worktreeRoot: root });
    fs.writeFileSync(path.join(created.root, "src", "main.py"), "print('agent work')\n");
    fs.writeFileSync(path.join(created.root, "src", "extra.py"), "y = 2\n");
    const kept = await worktree.remove(source, created.root, root, "thread-1");
    expect(kept).toMatch(/^[0-9a-f]{40}$/);
    expect(fs.existsSync(created.root)).toBe(false);
    expect(fs.existsSync(path.dirname(created.root))).toBe(false);
    expect(run(source, "for-each-ref", "--format=%(objectname)", "refs/mightling/snapshots/")).toBe(kept);
    const restored = await worktree.restore(source, kept!, root);
    expect(fs.readFileSync(path.join(restored.root, "src", "main.py"), "utf8")).toBe("print('agent work')\n");
    expect(fs.readFileSync(path.join(restored.root, "src", "extra.py"), "utf8")).toBe("y = 2\n");
    // Only the source's own managed worktrees can be removed.
    await expect(worktree.remove(source, source, root, null)).rejects.toThrow("not a managed worktree");
    await expect(worktree.remove(source, path.join(root, "abcd", "app"), root, null)).rejects.toThrow("is not a worktree of");
  });

  it("hand a thread's work to the local checkout and back, with its branch", async () => {
    const { source, root } = repo();
    const created = await worktree.create({ sourceCwd: source, base: null, withChanges: false, worktreeRoot: root });
    fs.writeFileSync(path.join(created.root, "src", "main.py"), "print('from the worktree')\n");
    await worktree.createBranch(created.root, "agent/fix");
    expect(run(created.root, "symbolic-ref", "--short", "HEAD")).toBe("agent/fix");
    await expect(worktree.createBranch(created.root, "bad..name")).rejects.toThrow();

    // To Local: the branch and the change move; the worktree is left clean and detached.
    fs.writeFileSync(path.join(source, "dirty.txt"), "local edit\n");
    await expect(worktree.handOff("toLocal", created.root, source, root, "t1")).rejects.toThrow("the local checkout has uncommitted changes");
    fs.rmSync(path.join(source, "dirty.txt"));
    const toLocal = await worktree.handOff("toLocal", created.root, source, root, "t1");
    expect(toLocal.snapshot).toMatch(/^[0-9a-f]{40}$/);
    expect(run(source, "symbolic-ref", "--short", "HEAD")).toBe("agent/fix");
    expect(fs.readFileSync(path.join(source, "src", "main.py"), "utf8")).toBe("print('from the worktree')\n");
    expect(run(created.root, "status", "--porcelain")).toBe("");
    expect(() => run(created.root, "symbolic-ref", "-q", "HEAD")).toThrow();
    // Ignored files stay where they are.
    expect(fs.existsSync(path.join(created.root, ".env"))).toBe(true);

    // Back to the worktree: the change moves again and the local checkout is clean.
    const back = await worktree.handOff("toWorktree", created.root, source, root, "t1");
    expect(back.snapshot).toMatch(/^[0-9a-f]{40}$/);
    expect(fs.readFileSync(path.join(created.root, "src", "main.py"), "utf8")).toBe("print('from the worktree')\n");
    expect(run(source, "status", "--porcelain")).toBe("");
    await expect(worktree.handOff("toLocal", source, source, root, "t1")).rejects.toThrow("not a managed worktree");
  });
});

describe("local environments", () => {
  it("are read from .codex/ and .mightling/, per platform, and written to .mightling/ only", () => {
    const { source } = repo();
    fs.mkdirSync(path.join(source, ".codex", "environments"), { recursive: true });
    fs.writeFileSync(path.join(source, ".codex", "environments", "environment.toml"), [
      'name = "Shared"',
      "[setup]",
      'script = "npm install"',
      'linux = "npm ci"',
      "[[actions]]",
      'name = "Run"',
      'icon = "play"',
      'command = "npm start"',
      "[[actions]]",
      'name = "Tests"',
      'icon = "test"',
      'script = "npm test"',
      'macos = "npm run test:mac"',
    ].join("\n"));
    const { found, problems } = environments(source, "linux");
    expect(problems).toEqual([]);
    expect(found).toEqual([{ file: ".codex/environments/environment.toml", folder: ".codex", name: "Shared", setup: "npm ci",
      actions: [{ name: "Run", icon: "play", script: "npm start" }, { name: "Tests", icon: "test", script: "npm test" }] }]);
    expect(readEnvironment('[[actions]]\nname = "T"\nscript = "a"\nmacos = "b"\n', ".codex/environment.toml", "darwin").actions[0].script).toBe("b");

    expect(writeEnvironment(source, { name: "Mine", setup: "uv sync", actions: [{ name: "Serve", icon: "globe", script: "uv run serve" }, { name: "empty", script: " " }] })).toBe(WRITE_FILE);
    expect(fs.existsSync(path.join(source, ".mightling", "environments", "default.toml"))).toBe(true);
    const after = environments(source, "linux").found;
    expect(after.map((entry) => [entry.folder, entry.name])).toEqual([[".mightling", "Mine"], [".codex", "Shared"]]);
    expect(after[0].actions).toEqual([{ name: "Serve", icon: "globe", script: "uv run serve" }]);
    // The .codex file is never rewritten.
    expect(fs.readFileSync(path.join(source, ".codex", "environments", "environment.toml"), "utf8")).toContain('name = "Shared"');
    fs.writeFileSync(path.join(source, ".mightling", "environment.toml"), "not = [toml");
    expect(environments(source, "linux").problems[0]).toMatch(/^\.mightling\/environment\.toml: /);
  });
});

describe("bottom-panel tasks", () => {
  it("run a script in the folder, stream its output and end", async () => {
    const events: TaskEvent[] = [];
    const ended = new Promise<void>((resolve) => {
      const tasks = new Tasks((event) => {
        events.push(event);
        if (event.kind === "exit") resolve();
      }, "/bin/sh");
      tasks.run("Run", "pwd; echo done", scratchHome);
      expect(() => tasks.run("Run", "", scratchHome)).toThrow("needs a script");
      expect(() => tasks.run("Run", "true", "relative")).toThrow("absolute");
    });
    await ended;
    expect(events[0]).toMatchObject({ kind: "started", label: "Run", cwd: scratchHome });
    expect(events.filter((event) => event.kind === "output").map((event) => event.data).join("")).toBe(`${fs.realpathSync(scratchHome)}\ndone\n`);
    expect(events[events.length - 1]).toMatchObject({ kind: "exit", code: 0 });
  });
});
