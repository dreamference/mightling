// Window-side git against scratch repositories (DESKTOP_PARITY §2.5): the scopes, stage, unstage and
// revert by file and by hunk, commit, the pull-request page, and the refusals that keep the page
// inside the repository. Each repository lives in a temporary folder with its own HOME and no
// global git configuration, so nothing of this machine's is read or written.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { findEditors, execProgram, openCommand, runsWhenOpened, systemDataDirs } from "./editors";
import * as git from "./git";

const scratchHome = fs.mkdtempSync(path.join(os.tmpdir(), "ling-git-home-"));
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

  it.skipIf(process.platform === "win32")("takes a file's name as written, not as a pattern for others", async () => {
    const root = repo();
    for (const name of ["[ab].txt", "b.txt"]) fs.writeFileSync(path.join(root, name), "x\n");
    await git.discard(root, ["[ab].txt"]);
    expect(fs.existsSync(path.join(root, "[ab].txt"))).toBe(false);
    expect(fs.existsSync(path.join(root, "b.txt"))).toBe(true);
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

describe("git on a checkout the agent wrote the config of", () => {
  it("runs no program the config names, on any read the window makes", async () => {
    const root = repo();
    // Forward slashes: a backslash in a config value is an escape, and sh wants them too.
    const marker = path.join(root, "..", `ran-${path.basename(root)}`).replace(/\\/g, "/");
    const tool = path.join(root, "..", `tool-${path.basename(root)}.sh`).replace(/\\/g, "/");
    fs.writeFileSync(tool, `#!/bin/sh\necho ran >> '${marker}'\ncat\n`, { mode: 0o755 });
    fs.writeFileSync(path.join(root, ".gitattributes"), "*.txt filter=evil diff=evil\n");
    const config = path.join(root, ".git", "config");
    fs.appendFileSync(
      config,
      [
        "[core]", `\tfsmonitor = ${tool}`, `\tpager = ${tool}`, `\tsshCommand = ${tool}`,
        "[diff]", `\texternal = ${tool}`,
        '[diff "evil"]', `\ttextconv = ${tool}`,
        '[filter "evil"]', `\tclean = ${tool}`, `\tsmudge = ${tool}`,
        "[credential]", `\thelper = ${tool}`,
        "",
      ].join("\n"),
    );
    fs.writeFileSync(path.join(root, "a.txt"), "changed once more\n");
    // The control: plain git does run the configured program here, so an empty marker means something.
    try {
      execFileSync("git", ["status", "--porcelain"], { cwd: root, encoding: "utf8", env: { ...process.env, GIT_CONFIG_NOSYSTEM: "1" } });
      execFileSync("git", ["diff"], { cwd: root, encoding: "utf8", env: { ...process.env, GIT_CONFIG_NOSYSTEM: "1", GIT_PAGER: "" } });
    } catch {
      /* a failing hook does not matter for the control */
    }
    expect(fs.existsSync(marker)).toBe(true);
    fs.rmSync(marker);

    await git.git(root, ["status", "--porcelain=v1", "--untracked-files=all"]);
    await git.git(root, ["diff"]);
    await git.git(root, ["diff", "--cached"]);
    await git.git(root, ["show", "HEAD"]);
    await git.git(root, ["log", "-p", "-1"]);
    await git.diff(root, "unstaged");
    expect(fs.existsSync(marker), "a configured program ran").toBe(false);
  });

  it("reads the system config (Git for Windows' autocrlf), but a program it names loses to the pins", async () => {
    const root = repo();
    const marker = path.join(root, "..", `ran-system-${path.basename(root)}`).replace(/\\/g, "/");
    const tool = path.join(root, "..", `tool-system-${path.basename(root)}.sh`).replace(/\\/g, "/");
    fs.writeFileSync(tool, `#!/bin/sh\necho ran >> '${marker}'\ncat\n`, { mode: 0o755 });
    const system = path.join(root, "..", `system-${path.basename(root)}.gitconfig`).replace(/\\/g, "/");
    fs.writeFileSync(
      system,
      ["[core]", "\tautocrlf = true", `\tfsmonitor = ${tool}`, `\tpager = ${tool}`, `\tsshCommand = ${tool}`, "[diff]", `\texternal = ${tool}`, "[credential]", `\thelper = ${tool}`, ""].join("\n"),
    );
    const asSystem = { GIT_CONFIG_SYSTEM: system, GIT_CONFIG_NOSYSTEM: "0" };
    // The control: plain git runs the system file's fsmonitor.
    try {
      execFileSync("git", ["status", "--porcelain"], { cwd: root, encoding: "utf8", env: { ...process.env, ...asSystem } });
    } catch {
      /* a failing hook does not matter for the control */
    }
    expect(fs.existsSync(marker)).toBe(true);
    fs.rmSync(marker);

    expect("GIT_CONFIG_NOSYSTEM" in git.gitEnv({})).toBe(false);
    await git.git(root, ["status", "--porcelain"], undefined, 60_000, asSystem);
    await git.git(root, ["diff"], undefined, 60_000, asSystem);
    expect((await git.git(root, ["config", "--get", "core.autocrlf"], undefined, 60_000, asSystem)).trim()).toBe("true");
    for (const [key, pin] of [["core.fsmonitor", "false"], ["core.sshCommand", "ssh"], ["core.pager", "cat"], ["credential.helper", ""], ["diff.external", ""]]) {
      expect((await git.git(root, ["config", "--get", key], undefined, 60_000, asSystem)).replace(/\n$/, ""), key).toBe(pin);
    }
    expect(fs.existsSync(marker), "a program the system config names ran").toBe(false);
  });

  it("lets no inherited variable move a config file or add config", () => {
    const env = git.gitEnv({
      PATH: "/usr/bin",
      GIT_CONFIG_SYSTEM: "/x",
      GIT_CONFIG_GLOBAL: "/x",
      GIT_CONFIG_COUNT: "1",
      GIT_CONFIG_KEY_0: "core.fsmonitor",
      GIT_CONFIG_VALUE_0: "/x",
      GIT_CONFIG_PARAMETERS: "'core.fsmonitor'='/x'",
    });
    expect(Object.keys(env).filter((name) => name.startsWith("GIT_CONFIG_"))).toEqual([]);
    expect(env.PATH).toBe("/usr/bin");
  });

  it("puts the diff flags after the subcommand and blanks only the checkout's own drivers", () => {
    expect(git.hardenedArgs(["diff", "HEAD"]).slice(-4)).toEqual(["diff", "--no-ext-diff", "--no-textconv", "HEAD"]);
    expect(git.hardenedArgs(["push", "origin"]).includes("--receive-pack=git-receive-pack")).toBe(true);
    const listing = ["system", "filter.lfs.clean\ngit-lfs clean", "local", "filter.evil.smudge\nrm -rf", "local", "core.bare\nfalse", ""].join("\0");
    expect(git.driverOverridesFrom(listing)).toEqual(["filter.evil.smudge="]);
  });

  it("names no git:// proxy (first value wins, so only the variable outranks the checkout's) and no signing-key command", () => {
    expect(git.gitEnv({ GIT_PROXY_COMMAND: "/tmp/x" }).GIT_PROXY_COMMAND).toBe("");
    expect(git.SAFE_CONFIG).toContain("gpg.ssh.defaultKeyCommand=");
  });

  it("parses porcelain v2 headers, renames and conflicts, and handles diff/apply/discard/PR URL edge cases", async () => {
    const raw = [
      "# branch.oid (initial)",
      "# branch.head (detached)",
      "# branch.upstream origin/main",
      "# branch.ab +2 -1",
      "1 M. N... 100644 100644 100644 aaaaaaa bbbbbbb mod.txt",
      "2 R. N... 100644 100644 100644 aaaaaaa bbbbbbb R100 new.txt",
      "old.txt",
      "u UU N... 100644 100644 100644 100644 aaaaaaa bbbbbbb ccccccc conflict.txt",
      "? untracked.txt",
      "",
    ].join("\0");
    const parsed = git.parseStatus("/repo", raw);
    expect(parsed).toMatchObject({
      root: "/repo",
      branch: null,
      head: null,
      upstream: "origin/main",
      ahead: 2,
      behind: 1,
    });
    expect(parsed.files.map((f) => [f.path, f.from, f.staged, f.unstaged])).toEqual([
      ["mod.txt", null, "M", "."],
      ["new.txt", "old.txt", "R", "."],
      ["conflict.txt", null, "U", "U"],
      ["untracked.txt", null, ".", "?"],
    ]);

    const drivers = [
      "worktree", "filter.a.process\nx",
      "local", "filter.a.required\ntrue",
      "local", "diff.b.textconv\nx",
      "local", "diff.b.command\nx",
      "local", "merge.c.driver\nx",
      "local", "submodule.d.update\n!sh",
      "local", "bad=key\nx",
      "",
    ].join("\0");
    expect(git.driverOverridesFrom(drivers)).toEqual([
      "diff.b.command=",
      "diff.b.textconv=",
      "filter.a.process=",
      "filter.a.required=false",
      "merge.c.driver=false",
      "submodule.d.update=none",
    ]);
    expect(git.hardenedArgs(["-c", "k=v", "-C", "/r", "--git-dir=/g", "--bare", "fetch", "origin"]).slice(-3)).toEqual([
      "fetch", "--upload-pack=git-upload-pack", "origin",
    ]);

    const r = repo();
    await expect(git.discard(r, [])).rejects.toThrow("name at least one file");
    await expect(git.apply(r, "   ", {})).rejects.toThrow();

    const unborn = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-git-unborn-")));
    run(unborn, "init", "-q");
    expect(await git.log(unborn)).toEqual([]);
    expect(await git.baseBranch(unborn)).toBeNull();
    expect((await git.diff(unborn, "branch")).diff).toBe("");
    expect(await git.originUrl(unborn)).toBeNull();
    await expect(git.pullRequestUrl(unborn)).rejects.toThrow();
    await expect(git.checkRoot("relative/path")).rejects.toThrow();

    run(r, "checkout", "-q", "-b", "feat/test");
    run(r, "remote", "add", "origin", "git@github.com:acme/widget.git");
    expect(await git.originUrl(r)).toBe("git@github.com:acme/widget.git");
    expect(await git.pullRequestUrl(r)).toContain("github.com/acme/widget/compare");
    expect((await git.branches(r)).current).toBe("feat/test");
    run(r, "remote", "set-url", "origin", "ssh://custom/repo");
    run(r, "checkout", "-q", "--detach");
    await expect(git.pullRequestUrl(r)).rejects.toThrow();
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
      expect(openCommand({ id: "sublime", name: "Sublime Text", command: "/bin/subl" }, "/r/a.txt", 9).args).toEqual(["/r/a.txt:9"]);
      expect(openCommand({ id: "gedit", name: "gedit", command: "/bin/gedit" }, "/r/a.txt", 9).args).toEqual(["+9", "/r/a.txt"]);
      expect(openCommand({ id: "gnome-text-editor", name: "Text Editor", command: "/bin/gte" }, "/r/a.txt", 9).args).toEqual(["/r/a.txt"]);
      expect(() => openCommand({ id: "unknown", name: "Other", command: "/bin/other" }, "/r/a.txt", 9)).toThrow("no editor named unknown");
    } finally {
      process.env.PATH = savedPath;
    }
    expect(execProgram("env BAMF=1 subl %F")).toBe("subl");
    expect(execProgram("%F")).toBeNull();
    expect(systemDataDirs().length).toBeGreaterThan(0);
  });

  it("never hand the system a file it would run instead of open (editor/open and docs/open with no editor chosen)", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-runs-"));
    const file = (name: string, mode = 0o644) => {
      const at = path.join(dir, name);
      fs.writeFileSync(at, "x");
      fs.chmodSync(at, mode);
      return at;
    };
    const notes = file("notes.md");
    const script = file("build.sh", 0o755);
    const entry = file("app.desktop");
    fs.symlinkSync(script, path.join(dir, "readme.txt"));
    const app = path.join(dir, "Tool.app");
    fs.mkdirSync(app);
    // Windows: what ShellExecute starts, whatever the case, trailing dots and spaces, a stream, PATHEXT's own.
    for (const name of ["setup.bat", "SETUP.EXE", "run.cmd.", "run.ps1 ", "go.lnk", "x.url", "a.hta", "s.js", "a.txt:evil.bat", "C:\\w\\tool.msi", "fix.py", "gui.pyw", "deploy.sh", "app.jar"]) {
      expect(await runsWhenOpened(path.join(dir, name), "win32", "")).toMatch(/would run, not open/);
    }
    expect(await runsWhenOpened(path.join(dir, "tool.xyz"), "win32", ".COM;.EXE;.XYZ")).toMatch(/would run/);
    for (const name of ["report.pdf", "notes.md", "photo.png", "data.csv"]) expect(await runsWhenOpened(path.join(dir, name), "win32", ".COM;.EXE")).toBeNull();
    // macOS: an application (a folder), a Terminal script, an installer, an executable; Linux: a launcher or an executable, also behind a link.
    expect(await runsWhenOpened(app, "darwin")).toMatch(/Tool.app would run/);
    for (const name of ["go.command", "x.pkg", "y.fileloc", "fix.py", "app.jar"]) expect(await runsWhenOpened(path.join(dir, name), "darwin")).toMatch(/would run/);
    expect(await runsWhenOpened(script, "darwin")).toMatch(/would run/);
    expect(await runsWhenOpened(entry, "linux")).toMatch(/app.desktop would run/);
    expect(await runsWhenOpened(script, "linux")).toMatch(/would run/);
    expect(await runsWhenOpened(path.join(dir, "app.jar"), "linux")).toMatch(/would run/);
    expect(await runsWhenOpened(path.join(dir, "readme.txt"), "linux")).toMatch(/readme.txt would run/);
    // Documents and folders open.
    expect(await runsWhenOpened(notes, "linux")).toBeNull();
    expect(await runsWhenOpened(notes, "darwin")).toBeNull();
    expect(await runsWhenOpened(dir, "linux")).toBeNull();
    expect(await runsWhenOpened(path.join(dir, "missing.md"), "linux")).toBeNull();
    fs.rmSync(dir, { recursive: true, force: true });
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
