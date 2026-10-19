// Window-side git against scratch repositories (DESKTOP_PARITY §2.5): the scopes, stage, unstage and
// revert by file and by hunk, commit, the pull-request page, and the refusals that keep the page
// inside the repository. Each repository lives in a temporary folder with its own HOME and no
// global git configuration, so nothing of this machine's is read or written.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { findEditors, execProgram, openCommand } from "./editors";
import * as git from "./git";

const scratchHome = fs.mkdtempSync(path.join(os.tmpdir(), "ling-git-home-"));
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

const run = (cwd: string, ...args: string[]) => execFileSync("git", args, { cwd, encoding: "utf8", env: git.gitEnv() });

const LINES = Array.from({ length: 40 }, (_, index) => `line ${index + 1}`);

/** A repository with one committed file of 40 lines, then two separate edits to it and a new file. */
function repo(): string {
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-git-")));
  run(root, "init", "-q");
  fs.writeFileSync(path.join(root, "a.txt"), LINES.join("\n") + "\n");
  run(root, "add", "a.txt");
  run(root, "commit", "-q", "-m", "first");
  const edited = [...LINES];
  edited[1] = "line 2 changed";
  edited[35] = "line 36 changed";
  fs.writeFileSync(path.join(root, "a.txt"), edited.join("\n") + "\n");
  fs.writeFileSync(path.join(root, "new.txt"), "hello\n");
  return root;
}

/** The hunks of a one-file diff, each as a patch of its own (what the page sends: desktop/ui/src/diff.ts). */
function hunkPatches(diff: string): string[] {
  const header = diff.slice(0, diff.indexOf("\n@@") + 1);
  return diff.slice(header.length).split(/\n(?=@@)/).map((hunk) => header + hunk.replace(/\n?$/, "\n"));
}

describe("window-side git", () => {
  it("knows a repository by its top level, and refuses anything else", async () => {
    const root = repo();
    await expect(git.checkRoot(root)).resolves.toBe(root);
    fs.mkdirSync(path.join(root, "sub"));
    await expect(git.checkRoot(path.join(root, "sub"))).rejects.toThrow("is not the top of its repository");
    await expect(git.checkRoot("relative/path")).rejects.toThrow("absolute path");
    await expect(git.checkRoot(scratchHome)).rejects.toThrow("is not a git repository");
    expect(() => git.insidePath(root, "../outside.txt")).toThrow("outside the repository");
    expect(() => git.insidePath(root, "/etc/passwd")).toThrow("outside the repository");
    expect(git.insidePath(root, "sub/../a.txt")).toBe("a.txt");
    expect(await git.repositories(path.join(root, "sub"))).toEqual([root]);
    // A folder that is not itself in a repository lists the repositories below it.
    const parent = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-git-parent-")));
    fs.renameSync(root, path.join(parent, "one"));
    expect(await git.repositories(parent)).toEqual([path.join(parent, "one")]);
  });

  it("shows the Unstaged scope with untracked files, and stages one hunk of two", async () => {
    const root = repo();
    const status = await git.status(root);
    expect(status.branch).toBe("main");
    expect(status.files).toEqual([
      { path: "a.txt", staged: ".", unstaged: "M", from: null },
      { path: "new.txt", staged: ".", unstaged: "?", from: null },
    ]);
    const unstaged = await git.diff(root, "unstaged");
    expect(unstaged.diff).toContain("+line 2 changed");
    expect(unstaged.diff).toContain("+line 36 changed");
    expect(unstaged.diff).toContain("+++ b/new.txt");

    const tracked = (await git.diff(root, "unstaged")).diff.split(/(?=^diff --git )/m).find((part) => part.includes("a/a.txt"))!;
    const [first, second] = hunkPatches(tracked);
    await git.apply(root, first, { cached: true });
    const staged = await git.diff(root, "staged");
    expect(staged.diff).toContain("+line 2 changed");
    expect(staged.diff).not.toContain("line 36 changed");
    expect((await git.diff(root, "unstaged")).diff).toContain("+line 36 changed");

    // Unstage it again, then revert the second hunk in the work tree.
    await git.apply(root, first, { cached: true, reverse: true });
    expect((await git.diff(root, "staged")).diff).toBe("");
    await git.apply(root, second, { reverse: true });
    const text = fs.readFileSync(path.join(root, "a.txt"), "utf8");
    expect(text).toContain("line 2 changed");
    expect(text).not.toContain("line 36 changed");
  });

  it("stages, unstages and discards whole files; an untracked file is deleted only when asked", async () => {
    const root = repo();
    await git.stage(root, ["a.txt", "new.txt"]);
    expect((await git.status(root)).files.map((file) => `${file.staged}${file.unstaged} ${file.path}`)).toEqual(["M. a.txt", "A. new.txt"]);
    await git.unstage(root, ["new.txt"]);
    expect((await git.status(root)).files.find((file) => file.path === "new.txt")?.unstaged).toBe("?");
    await git.discard(root, ["new.txt"]);
    expect(fs.existsSync(path.join(root, "new.txt"))).toBe(false);
    await expect(git.stage(root, ["../escape"])).rejects.toThrow("outside the repository");
    await expect(git.apply(root, "--- a/../x\n+++ b/../x\n@@ -1 +1 @@\n-a\n+b\n", { cached: true })).rejects.toThrow("outside the repository");
  });

  it("commits what is staged, or everything, and shows the Commit and Branch scopes", async () => {
    const root = repo();
    run(root, "checkout", "-q", "-b", "feature");
    await expect(git.commit(root, "  ", false)).rejects.toThrow("needs a message");
    const sha = await git.commit(root, "Change two lines\n\nAnd add a file.", true);
    expect(run(root, "log", "-1", "--format=%B").trim()).toBe("Change two lines\n\nAnd add a file.");
    expect((await git.status(root)).files).toEqual([]);
    const shown = await git.diff(root, "commit", sha);
    expect(shown.diff).toContain("+line 36 changed");
    expect((await git.log(root)).map((entry) => entry.subject)).toEqual(["Change two lines", "first"]);
    const branch = await git.diff(root, "branch");
    expect(branch.against).toBe("main");
    expect(branch.diff).toContain("+++ b/new.txt");
  });

  it("refuses to push a detached HEAD, and builds the forge's pull-request page", async () => {
    const root = repo();
    run(root, "checkout", "-q", "--detach");
    await expect(git.push(root, false)).rejects.toThrow("detached");
    expect(git.pullRequestPage("git@github.com:dreamference/mightling.git", "feature/x", "origin/main"))
      .toBe("https://github.com/dreamference/mightling/compare/main...feature/x?expand=1");
    expect(git.pullRequestPage("https://github.com/dreamference/mightling", "fix", null))
      .toBe("https://github.com/dreamference/mightling/compare/fix?expand=1");
    expect(git.pullRequestPage("ssh://git@gitlab.example.com:2222/team/app.git", "fix", "main"))
      .toBe("https://gitlab.example.com/team/app/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix&merge_request%5Btarget_branch%5D=main");
    expect(git.pullRequestPage("https://forge.example.org/team/app.git", "fix", "main")).toBe("https://forge.example.org/team/app");
    expect(git.pullRequestPage("/srv/git/app.git", "fix", "main")).toBeNull();
  });
});

describe("editors", () => {
  it("are found from .desktop entries and opened at a line with their own arguments", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-editors-"));
    const apps = path.join(home, ".local", "share", "applications");
    fs.mkdirSync(apps, { recursive: true });
    fs.writeFileSync(path.join(apps, "code.desktop"), "[Desktop Entry]\nName=Visual Studio Code\nExec=/usr/share/code/code --unity-launch %F\n");
    fs.writeFileSync(path.join(apps, "pycharm.desktop"), "[Desktop Entry]\nName=PyCharm\nExec=\"/opt/pycharm/bin/pycharm.sh\" %f\n");
    fs.writeFileSync(path.join(apps, "other.desktop"), "[Desktop Entry]\nName=Calculator\nExec=gnome-calculator\n");
    const savedPath = process.env.PATH;
    process.env.PATH = "";
    try {
      const found = findEditors(home, []);
      expect(found).toEqual([
        { id: "code", name: "VS Code", command: "/usr/share/code/code" },
        { id: "pycharm", name: "PyCharm", command: "/opt/pycharm/bin/pycharm.sh" },
      ]);
      expect(openCommand(found[0], "/r/a.txt", 12)).toEqual({ command: "/usr/share/code/code", args: ["--goto", "/r/a.txt:12"] });
      expect(openCommand(found[1], "/r/a.txt", 12)).toEqual({ command: "/opt/pycharm/bin/pycharm.sh", args: ["--line", "12", "/r/a.txt"] });
      expect(openCommand(found[1], "/r/a.txt", null).args).toEqual(["/r/a.txt"]);
    } finally {
      process.env.PATH = savedPath;
    }
    expect(execProgram("env BAMF=1 subl %F")).toBe("subl");
    expect(execProgram("%F")).toBeNull();
  });
});

describe("git on a Mac without the Command Line Tools", () => {
  // The stub git and xcode-select are shell scripts: the check itself runs only on macOS, and is tested here by name.
  const unix = process.platform !== "win32";
  /** A folder with an executable `name` that runs `body`, and records each run in `runs.txt`. */
  const program = (name: string, body: string) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-git-tools-"));
    const file = path.join(dir, name);
    fs.writeFileSync(file, `#!/bin/sh\necho run >> "${path.join(dir, "runs.txt")}"\n${body}\n`);
    fs.chmodSync(file, 0o755);
    return { dir, file, runs: () => (fs.existsSync(path.join(dir, "runs.txt")) ? fs.readFileSync(path.join(dir, "runs.txt"), "utf8").split("\n").filter(Boolean).length : 0) };
  };

  it.runIf(unix)("never offers the stub, says why, and uses a real git when there is one", () => {
    const stub = program("git", "echo 'the stub ran' >&2; exit 1");
    const noTools = program("xcode-select", "echo 'xcode-select: error: unable to get active developer directory' >&2; exit 2");
    const darwin = { platform: "darwin" as const, pathEnv: stub.dir, stub: stub.file };
    // Not a Mac: nothing to check.
    expect(git.gitMissing({ ...darwin, platform: "linux" })).toBeNull();
    // The stub alone and no developer folder: missing, with the hint, and the stub never ran.
    expect(git.gitMissing({ ...darwin, xcodeSelect: noTools.file })).toBe(git.NO_COMMAND_LINE_TOOLS);
    expect(git.NO_COMMAND_LINE_TOOLS).toContain("xcode-select --install");
    // A developer folder named that has no git in it is no better.
    const empty = fs.mkdtempSync(path.join(os.tmpdir(), "ling-git-developer-"));
    const emptyFolder = program("xcode-select", `echo ${empty}`);
    expect(git.gitMissing({ ...darwin, xcodeSelect: emptyFolder.file })).toBe(git.NO_COMMAND_LINE_TOOLS);
    // The tools installed: xcode-select names a folder with usr/bin/git, and git runs.
    fs.mkdirSync(path.join(empty, "usr", "bin"), { recursive: true });
    fs.writeFileSync(path.join(empty, "usr", "bin", "git"), "");
    expect(git.gitMissing({ ...darwin, xcodeSelect: emptyFolder.file })).toBeNull();
    // A git before the stub on PATH (Homebrew's) is used without asking xcode-select.
    const brew = program("git", "exit 0");
    const asked = program("xcode-select", "exit 2");
    expect(git.gitMissing({ ...darwin, pathEnv: [brew.dir, stub.dir].join(path.delimiter), xcodeSelect: asked.file })).toBeNull();
    expect(asked.runs()).toBe(0);
    expect(stub.runs()).toBe(0);
  });
});
