// The review pane's selector counts (DESKTOP §16 B32): lines changed against HEAD per repository, from
// scratch repositories with their own HOME and no global git configuration.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { gitEnv } from "./git";
import { changedLines, parseNumstat } from "./repolines";

const scratchHome = fs.mkdtempSync(path.join(os.tmpdir(), "ling-repolines-home-"));
const saved = { HOME: process.env.HOME, GIT_CONFIG_GLOBAL: process.env.GIT_CONFIG_GLOBAL, GIT_CONFIG_NOSYSTEM: process.env.GIT_CONFIG_NOSYSTEM };

beforeAll(() => {
  process.env.HOME = scratchHome;
  process.env.GIT_CONFIG_GLOBAL = path.join(scratchHome, ".gitconfig");
  process.env.GIT_CONFIG_NOSYSTEM = "1";
  fs.writeFileSync(process.env.GIT_CONFIG_GLOBAL, "[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n");
});
afterAll(() => {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

const run = (cwd: string, ...args: string[]) => execFileSync("git", args, { cwd, encoding: "utf8", env: gitEnv() });

describe("the lines a repository changed", () => {
  it("sums git's numstat and leaves binary files out", () => {
    expect(parseNumstat("3\t1\ta.txt\n10\t0\tb/c.py\n-\t-\timage.png\n")).toEqual({ added: 13, removed: 1 });
    expect(parseNumstat("")).toEqual({ added: 0, removed: 0 });
  });

  it("counts staged and unstaged edits against HEAD, and a repository with no commit against nothing", async () => {
    const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-repolines-")));
    fs.writeFileSync(path.join(root, "a.txt"), "one\ntwo\nthree\n");
    run(root, "init", "-q");
    run(root, "add", "a.txt");
    expect(await changedLines(root)).toEqual({ added: 3, removed: 0 });
    run(root, "commit", "-q", "-m", "first");
    expect(await changedLines(root)).toEqual({ added: 0, removed: 0 });
    fs.writeFileSync(path.join(root, "a.txt"), "one\n2\nthree\nfour\n");
    run(root, "add", "a.txt");
    fs.appendFileSync(path.join(root, "a.txt"), "five\n");
    fs.writeFileSync(path.join(root, "new.txt"), "not added yet\n");
    expect(await changedLines(root)).toEqual({ added: 3, removed: 1 });
  });
});
