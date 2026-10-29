// Window-side git (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §1.2, §2.5): the Codex app runs
// `git` from its main process for everything the protocol has no method for (the review pane's
// scopes, stage and revert by file and by hunk, commit, push, the pull-request page), and so does
// this one. Every call is `git` with an argument list (never a shell), in a repository the page
// names and this module checks; paths stay inside it; a patch arrives on stdin; nothing here reaches
// the network except `push`, which only a click runs and which main.ts refuses at `/airgapped on`.
// No Electron here, so this tests against scratch repositories.

import { execFile, execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import * as userFs from "./userfs";

/** The most a diff or log may weigh: the pane is for reading, and this machine's memory is shared with a model. */
export const MAX_OUTPUT = 16 * 1024 * 1024;

/** The variables that add config or move the system or global config file: never inherited. */
export const REMOVED_CONFIG_ENV = ["GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_COUNT"];

/** git's environment: never a prompt for credentials (a click must not hang), never a pager or an editor. */
export function gitEnv(base: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  // No GIT_CONFIG_NOSYSTEM: the system config is trusted like the global one, since only an
  // administrator can write it (a per-user Git for Windows install keeps it in the user's own install
  // folder, the user's, not the checkout's); it carries Git for Windows' core.autocrlf=true and its
  // git-lfs filter, and a program it names loses to SAFE_CONFIG. No variable may move it or add config.
  const env: NodeJS.ProcessEnv = { ...base, GIT_TERMINAL_PROMPT: "0", GIT_PAGER: "cat", PAGER: "cat", GIT_EDITOR: "true", LC_ALL: "C", GIT_OPTIONAL_LOCKS: "0" };
  delete env.GIT_EXTERNAL_DIFF;
  for (const name of Object.keys(env)) {
    if (REMOVED_CONFIG_ENV.includes(name) || name.startsWith("GIT_CONFIG_KEY_") || name.startsWith("GIT_CONFIG_VALUE_")) delete env[name];
  }
  // Empty, not deleted: core.gitProxy is first-value-wins, so only the variable outranks the checkout's own value.
  env.GIT_PROXY_COMMAND = "";
  return env;
}

/**
 * Config pinned on every command: a checkout's `.git/config` is writable by the agent while this
 * runs outside the sandbox, and these keys make git execute a program (`core.fsmonitor` on status,
 * `core.pager`, `diff.external`, `core.sshCommand` and `core.gitProxy` on push, `credential.helper`,
 * `gpg.program`, the `ext::` transport). The command line outranks every config file.
 * The keys whose names the agent chooses (`filter.<n>.clean`, `diff.<n>.textconv`, ...) are read
 * from the checkout's own config by `driverOverrides` and blanked there. Repository hooks DO run from here
 * (owner's choice 2026-10-28; see docs/dev/host-safety.md).
 */
export const SAFE_CONFIG = [
  "core.fsmonitor=false",
  "core.pager=cat",
  "core.sshCommand=ssh",
  "core.gitProxy=",
  "core.askPass=",
  "core.editor=true",
  "sequence.editor=true",
  "diff.external=",
  "credential.helper=",
  "gpg.program=gpg",
  "gpg.ssh.program=ssh-keygen",
  "gpg.x509.program=gpgsm",
  "gpg.openpgp.program=gpg",
  "gpg.ssh.defaultKeyCommand=",
  "log.showSignature=false",
  "protocol.ext.allow=never",
  "submodule.recurse=false",
  "status.submoduleSummary=false",
  "gc.auto=0",
  "maintenance.auto=false",
];

/** Flags that config cannot override, placed right after the subcommand (the remote's program keys are first-value-wins in git). */
const SUBCOMMAND_FLAGS: Record<string, string[]> = {
  diff: ["--no-ext-diff", "--no-textconv"],
  show: ["--no-ext-diff", "--no-textconv"],
  log: ["--no-ext-diff", "--no-textconv"],
  fetch: ["--upload-pack=git-upload-pack"],
  pull: ["--upload-pack=git-upload-pack"],
  "ls-remote": ["--upload-pack=git-upload-pack"],
  push: ["--receive-pack=git-receive-pack"],
};

/** The `-c key=` pairs that blank each driver program the checkout's own config (not the user's) names. */
export function driverOverridesFrom(listing: string): string[] {
  const out = new Set<string>();
  const fields = listing.split("\0");
  for (let i = 0; i + 1 < fields.length; i += 2) {
    const scope = fields[i];
    if (scope !== "local" && scope !== "worktree" && scope !== "command") continue;
    const key = fields[i + 1].split("\n")[0];
    const m = /^(filter|diff|merge|submodule)\.(.+)\.([^.]+)$/.exec(key);
    if (!m || m[2].includes("=")) continue;
    const variable = m[3].toLowerCase();
    let blank: string | null = null;
    if (m[1] === "filter" && ["clean", "smudge", "process"].includes(variable)) blank = "";
    else if (m[1] === "filter" && variable === "required") blank = "false";
    else if (m[1] === "diff" && ["textconv", "command"].includes(variable)) blank = "";
    else if (m[1] === "merge" && variable === "driver") blank = "false";
    else if (m[1] === "submodule" && variable === "update") blank = "none";
    if (blank !== null) out.add(`${key}=${blank}`);
  }
  return [...out].sort();
}

/** What the checkout's own config names as driver programs, as `-c` pairs; none when git cannot say. */
export function driverOverrides(cwd: string): Promise<string[]> {
  return new Promise((resolve) => {
    execFile("git", ["-C", cwd, "config", "--list", "--null", "--show-scope"], { env: gitEnv(), timeout: 10_000, maxBuffer: MAX_OUTPUT, encoding: "utf8" }, (error, stdout) => {
      resolve(error ? [] : driverOverridesFrom(stdout));
    });
  });
}

/** `args` with the safe config before the subcommand and the unoverridable flags after it. */
export function hardenedArgs(args: string[], drivers: string[] = []): string[] {
  const prefix = ["--no-pager"];
  for (const pair of [...SAFE_CONFIG, ...drivers]) prefix.push("-c", pair);
  let i = 0;
  while (i < args.length && args[i].startsWith("-")) i += ["-c", "-C", "--git-dir", "--work-tree"].includes(args[i]) ? 2 : 1;
  const extra = i < args.length ? SUBCOMMAND_FLAGS[args[i]] : undefined;
  return [...prefix, ...args.slice(0, i + 1), ...(extra ?? []), ...args.slice(i + 1)];
}

export class GitError extends Error {}

/** What the page shows when a Mac has only the stub `git`. */
export const NO_COMMAND_LINE_TOOLS =
  "Git needs Apple's Command Line Tools. Install them once in Terminal with xcode-select --install, then reopen the project.";

/** Where `gitMissing` looks; each is this machine's own unless a test names another. */
export interface GitToolsOptions {
  platform?: NodeJS.Platform;
  /** The PATH `git` is found on. */
  pathEnv?: string;
  /** The macOS stub: a `git` that only offers to install the Command Line Tools. */
  stub?: string;
  xcodeSelect?: string;
}

/**
 * Why window-side git cannot run here, or null when it can; it never runs `git` itself. On macOS,
 * `/usr/bin/git` is a stub until Apple's Command Line Tools (or Xcode) are installed, and every run of
 * it pops the system's "install the command line developer tools" dialog, so the stub is run only
 * when `xcode-select -p` names a developer folder with a real git in it. A `git` found on PATH
 * before the stub (Homebrew's, say) is used as it is.
 */
export function gitMissing(options: GitToolsOptions = {}): string | null {
  const platform = options.platform ?? process.platform;
  if (platform !== "darwin") return null;
  const stub = options.stub ?? "/usr/bin/git";
  const found = (options.pathEnv ?? process.env.PATH ?? "")
    .split(path.delimiter)
    .filter(Boolean)
    .map((dir) => path.join(dir, "git"))
    .find((file) => {
      try {
        fs.accessSync(file, fs.constants.X_OK);
        return fs.statSync(file).isFile();
      } catch {
        return false;
      }
    });
  if (found && path.resolve(found) !== path.resolve(stub)) return null;
  try {
    const developer = execFileSync(options.xcodeSelect ?? "/usr/bin/xcode-select", ["-p"], { encoding: "utf8", timeout: 5_000, stdio: ["ignore", "pipe", "ignore"] }).trim();
    if (developer && fs.existsSync(path.join(developer, "usr", "bin", "git"))) return null;
  } catch {
    // No developer folder chosen: the tools are not installed.
  }
  return NO_COMMAND_LINE_TOOLS;
}

/** This machine's answer, kept once git is there and asked again after a while while it is not (the tools may arrive). */
let toolsAnswer: { missing: string | null; at: number } | null = null;
export function gitToolsMissing(now = Date.now()): string | null {
  if (!toolsAnswer || (toolsAnswer.missing !== null && now - toolsAnswer.at > 30_000)) toolsAnswer = { missing: gitMissing(), at: now };
  return toolsAnswer.missing;
}

/** Runs `git args` in `cwd`; resolves with stdout, rejects with git's own message. */
export function git(cwd: string, args: string[], input?: string, timeoutMs = 60_000, extraEnv: Record<string, string> = {}): Promise<string> {
  const missing = gitToolsMissing();
  if (missing) return Promise.reject(new GitError(missing));
  return driverOverrides(cwd).then((drivers) => new Promise<string>((resolve, reject) => {
    const child = execFile("git", hardenedArgs(args, drivers), { cwd, env: { ...gitEnv(), ...extraEnv }, maxBuffer: MAX_OUTPUT, timeout: timeoutMs, encoding: "utf8" }, (error, stdout, stderr) => {
      if (error) {
        const tooBig = (error as { code?: string }).code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER";
        reject(new GitError(tooBig ? "the output is too large to show here" : String(stderr || error.message).trim()));
      } else resolve(stdout);
    });
    if (input !== undefined) child.stdin?.end(input);
  }));
}

/** The repository `root` really is: absolute, and git's own top level for it. */
export async function checkRoot(root: unknown): Promise<string> {
  if (typeof root !== "string" || !path.isAbsolute(root)) throw new GitError("a repository is named by its absolute path");
  let top: string;
  try {
    top = (await git(root, ["rev-parse", "--show-toplevel"])).trim();
  } catch {
    throw new GitError(`${root} is not a git repository`);
  }
  // git answers with forward slashes and long names; Windows may hand us a short (8.3) name.
  if (!(await userFs.samePath(top, root))) throw new GitError(`${root} is not the top of its repository (${top} is)`);
  return userFs.realPath(top);
}

/** A path inside the repository, relative to its root; anything that would leave it is refused. */
export function insidePath(root: string, file: unknown): string {
  if (typeof file !== "string" || !file || file.includes("\0")) throw new GitError("a path is needed");
  const resolved = path.resolve(root, file);
  const relative = path.relative(root, resolved);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) throw new GitError(`${file} is outside the repository`);
  return relative;
}

const paths = (root: string, files: unknown): string[] => {
  if (!Array.isArray(files) || files.length === 0) throw new GitError("name at least one file");
  return files.map((file) => insidePath(root, file));
};

/**
 * The repositories a thread's folder belongs to: the one it is inside, or, for a folder that is not
 * itself in one, the repositories directly below it (two levels), for the pane's selector.
 */
export async function repositories(cwd: unknown): Promise<string[]> {
  // Every look at the folder is asynchronous and bounded (userfs.ts): a folder macOS guards (under
  // ~/Downloads, say) waits on the access prompt, which froze the app when this walk was synchronous.
  try {
    if (typeof cwd !== "string" || !path.isAbsolute(cwd) || !(await userFs.exists(cwd))) return [];
    // Without a git to run, there is no repository to show (the page shows why: `gitToolsMissing`).
    if (gitToolsMissing()) return [];
    try {
      return [await userFs.realPath((await git(cwd, ["rev-parse", "--show-toplevel"])).trim())];
    } catch (error) {
      if (error instanceof userFs.UserFsTimeout) throw error;
      // Not inside a repository: look a little way down.
    }
    const found: string[] = [];
    const look = async (folder: string, depth: number): Promise<void> => {
      let entries: fs.Dirent[];
      try {
        entries = await userFs.readdir(folder);
      } catch (error) {
        if (error instanceof userFs.UserFsTimeout) throw error;
        return;
      }
      for (const entry of entries) {
        if (!entry.isDirectory() || entry.name === "node_modules" || entry.name.startsWith(".")) continue;
        const child = path.join(folder, entry.name);
        if (await userFs.exists(path.join(child, ".git"))) found.push(child);
        else if (depth > 1) await look(child, depth - 1);
        if (found.length >= 20) return;
      }
    };
    await look(cwd, 2);
    return found.sort();
  } catch (error) {
    // A folder that does not answer has no repository to show yet; the pane asks again later.
    if (error instanceof userFs.UserFsTimeout) return [];
    throw error;
  }
}

export interface FileStatus {
  path: string;
  /** Index (staged) and work-tree (unstaged) states, git's letters: M, A, D, R, C, U, or "." for none; "?" for untracked. */
  staged: string;
  unstaged: string;
  /** The old path of a rename. */
  from: string | null;
}

export interface Status {
  root: string;
  branch: string | null;
  head: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  files: FileStatus[];
}

/** `git status --porcelain=v2 --branch -z`, parsed. */
export function parseStatus(root: string, text: string): Status {
  const status: Status = { root, branch: null, head: null, upstream: null, ahead: 0, behind: 0, files: [] };
  const fields = text.split("\0");
  for (let index = 0; index < fields.length; index++) {
    const line = fields[index];
    if (!line) continue;
    if (line.startsWith("# branch.oid ")) status.head = line.slice(13) === "(initial)" ? null : line.slice(13);
    else if (line.startsWith("# branch.head ")) status.branch = line.slice(14) === "(detached)" ? null : line.slice(14);
    else if (line.startsWith("# branch.upstream ")) status.upstream = line.slice(18);
    else if (line.startsWith("# branch.ab ")) {
      const match = line.match(/\+(\d+) -(\d+)/);
      if (match) [status.ahead, status.behind] = [Number(match[1]), Number(match[2])];
    } else if (line.startsWith("1 ")) {
      const parts = line.split(" ");
      status.files.push({ path: parts.slice(8).join(" "), staged: parts[1][0], unstaged: parts[1][1], from: null });
    } else if (line.startsWith("2 ")) {
      const parts = line.split(" ");
      status.files.push({ path: parts.slice(9).join(" "), staged: parts[1][0], unstaged: parts[1][1], from: fields[++index] ?? null });
    } else if (line.startsWith("u ")) {
      const parts = line.split(" ");
      status.files.push({ path: parts.slice(10).join(" "), staged: "U", unstaged: "U", from: null });
    } else if (line.startsWith("? ")) {
      status.files.push({ path: line.slice(2), staged: ".", unstaged: "?", from: null });
    }
  }
  return status;
}

export async function status(root: string): Promise<Status> {
  return parseStatus(root, await git(root, ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"]));
}

export type Scope = "unstaged" | "staged" | "commit" | "branch";

const DIFF_FLAGS = ["--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "-U3"];

/** The branch a feature branch is compared with: the remote's default, else main or master. */
export async function baseBranch(root: string): Promise<string | null> {
  try {
    const head = (await git(root, ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])).trim();
    if (head) return head.replace(/^refs\/remotes\//, "");
  } catch {
    // No remote default.
  }
  for (const name of ["main", "master", "origin/main", "origin/master"]) {
    try {
      await git(root, ["rev-parse", "--verify", "--quiet", `${name}^{commit}`]);
      return name;
    } catch {
      // Try the next.
    }
  }
  return null;
}

/** A new file too large (or too odd) to show, as a one-line stand-in diff. */
function notShown(file: string, why: string): string {
  return `diff --git a/${file} b/${file}\nnew file mode 100644\n--- /dev/null\n+++ b/${file}\n@@ -0,0 +1 @@\n+(not shown: ${why})\n`;
}

/** `git diff --no-index` exits 1 on a difference; this runs it and keeps the output. */
function diffNoIndex(root: string, file: string): Promise<string> {
  return new Promise((resolve) => {
    if (gitToolsMissing()) return resolve("");
    void driverOverrides(root).then((drivers) => {
      execFile("git", hardenedArgs(["diff", ...DIFF_FLAGS, "--no-index", "--", "/dev/null", file], drivers), { cwd: root, env: gitEnv(), maxBuffer: MAX_OUTPUT, encoding: "utf8" },
        (_error, stdout) => resolve(stdout));
    });
  });
}

export interface DiffAnswer {
  scope: Scope;
  diff: string;
  /** What the diff is against, in words: the base branch, the commit. */
  against: string | null;
}

/** The diff of one scope (Last turn is the server's, `turn/diff/updated`). */
export async function diff(root: string, scope: Scope, commit?: string | null): Promise<DiffAnswer> {
  switch (scope) {
    case "unstaged": {
      const tracked = await git(root, ["diff", ...DIFF_FLAGS]);
      const untracked = (await status(root)).files.filter((file) => file.unstaged === "?").slice(0, 200);
      const added: string[] = [];
      for (const file of untracked) {
        const stat = await userFs.statOrNull(path.join(root, file.path));
        if (!stat || !stat.isFile()) added.push(notShown(file.path, "not a regular file"));
        else if (stat.size > 1024 * 1024) added.push(notShown(file.path, "larger than 1 MB"));
        else added.push(await diffNoIndex(root, file.path));
      }
      return { scope, diff: tracked + added.join(""), against: "the index" };
    }
    case "staged":
      return { scope, diff: await git(root, ["diff", "--cached", ...DIFF_FLAGS]), against: "HEAD" };
    case "commit": {
      const sha = typeof commit === "string" && /^[0-9a-fA-F]{4,64}$/.test(commit) ? commit : "HEAD";
      return { scope, diff: await git(root, ["show", "--format=", ...DIFF_FLAGS, sha, "--"]), against: sha };
    }
    case "branch": {
      const base = await baseBranch(root);
      if (!base) return { scope, diff: "", against: null };
      const mergeBase = (await git(root, ["merge-base", base, "HEAD"])).trim();
      return { scope, diff: await git(root, ["diff", ...DIFF_FLAGS, mergeBase]), against: base };
    }
  }
}

export interface Commit {
  sha: string;
  subject: string;
  author: string;
  time: number;
}

export async function log(root: string, count = 30): Promise<Commit[]> {
  let text: string;
  try {
    text = await git(root, ["log", `-n${Math.max(1, Math.min(200, count))}`, "--format=%H%x1f%s%x1f%an%x1f%at%x1e"]);
  } catch {
    return [];
  }
  return text.split("\x1e").map((record) => record.trim()).filter(Boolean).map((record) => {
    const [sha, subject, author, time] = record.split("\x1f");
    return { sha, subject, author, time: Number(time) };
  });
}

/** A file the page names is that file: `[ab].txt` or `c*.txt` is not a pattern that also takes `b.txt` or `cd.txt`. */
const LITERAL = { GIT_LITERAL_PATHSPECS: "1" };

export const stage = async (root: string, files: unknown) => void (await git(root, ["add", "--", ...paths(root, files)], undefined, undefined, LITERAL));
export const unstage = async (root: string, files: unknown) => void (await git(root, ["restore", "--staged", "--", ...paths(root, files)], undefined, undefined, LITERAL));

/** Throws away a file's unstaged changes; an untracked file is deleted (the page asks first). */
export async function discard(root: string, files: unknown): Promise<void> {
  const list = paths(root, files);
  const untracked = new Set((await status(root)).files.filter((file) => file.unstaged === "?").map((file) => file.path));
  const tracked = list.filter((file) => !untracked.has(file));
  if (tracked.length) await git(root, ["restore", "--worktree", "--", ...tracked], undefined, undefined, LITERAL);
  const gone = list.filter((file) => untracked.has(file));
  if (gone.length) await git(root, ["clean", "-f", "-q", "--", ...gone], undefined, undefined, LITERAL);
}

/** The files a patch touches, from its headers; every one must stay inside the repository. */
export function patchPaths(patch: string): string[] {
  const found = new Set<string>();
  for (const match of patch.matchAll(/^(?:---|\+\+\+) (?:[ab]\/)?(.+)$/gm)) {
    if (match[1] !== "/dev/null") found.add(match[1].replace(/\t.*$/, ""));
  }
  return [...found];
}

/**
 * Applies a patch the page built from one hunk: to the index (stage), reversed to the index
 * (unstage), reversed to the work tree (revert), or reversed to both (revert a staged hunk).
 */
export async function apply(root: string, patch: unknown, options: { cached?: boolean; reverse?: boolean; index?: boolean; zeroContext?: boolean }): Promise<void> {
  if (typeof patch !== "string" || !patch.includes("@@") || patch.length > MAX_OUTPUT) throw new GitError("not a patch");
  for (const file of patchPaths(patch)) insidePath(root, file);
  const args = ["apply", "--whitespace=nowarn", "--recount"];
  if (options.cached) args.push("--cached");
  else if (options.index) args.push("--index");
  if (options.reverse) args.push("--reverse");
  // An edit in the review pane whose change starts or ends at its hunk's edge (desktop/ui/src/diff.ts `zeroContext`).
  if (options.zeroContext) args.push("--unidiff-zero");
  await git(root, [...args, "-"], patch);
}

/** Commits what is staged, or everything first when `all` is set; resolves with the new commit's sha. */
export async function commit(root: string, message: unknown, all: boolean): Promise<string> {
  if (typeof message !== "string" || !message.trim()) throw new GitError("a commit needs a message");
  if (all) await git(root, ["add", "-A"]);
  await git(root, ["commit", "--quiet", "--file=-"], message.trim() + "\n");
  return (await git(root, ["rev-parse", "HEAD"])).trim();
}

/** Pushes the current branch, setting its upstream the first time; a click runs it, never a timer. */
export async function push(root: string, force: boolean): Promise<string> {
  const current = await status(root);
  if (!current.branch) throw new GitError("HEAD is detached: create a branch before pushing");
  const args = ["push", "--porcelain"];
  if (force) args.push("--force-with-lease");
  if (!current.upstream) args.push("--set-upstream", "origin", current.branch);
  return (await git(root, args, undefined, 300_000)).trim();
}

/**
 * The forge's page for a new pull request from `branch` into `base`, prefilled, for the system's
 * browser: GitHub's compare page, GitLab's new merge request, or the repository's page. No API, no
 * token: the forge does the rest in the user's own browser session.
 */
export function pullRequestPage(remote: string, branch: string, base: string | null): string | null {
  let host: string;
  let repo: string;
  const ssh = remote.match(/^(?:[^@/]+@)?([^:/]+):(?!\/)(.+?)(?:\.git)?\/?$/);
  const url = remote.match(/^(?:https?|ssh|git):\/\/(?:[^@/]+@)?([^/:]+)(?::\d+)?\/(.+?)(?:\.git)?\/?$/);
  if (url) [host, repo] = [url[1], url[2]];
  else if (ssh) [host, repo] = [ssh[1], ssh[2]];
  else return null;
  const encode = (value: string) => value.split("/").map(encodeURIComponent).join("/");
  const target = base?.replace(/^origin\//, "") ?? null;
  if (host === "github.com" || host.startsWith("github.")) {
    return `https://${host}/${repo}/compare/${target ? `${encode(target)}...` : ""}${encode(branch)}?expand=1`;
  }
  if (host.includes("gitlab")) {
    const query = new URLSearchParams({ "merge_request[source_branch]": branch, ...(target ? { "merge_request[target_branch]": target } : {}) });
    return `https://${host}/${repo}/-/merge_requests/new?${query.toString()}`;
  }
  return `https://${host}/${repo}`;
}

/** The repository's `origin` remote, or null when it has none. */
export async function originUrl(root: string): Promise<string | null> {
  try {
    const remote = (await git(root, ["remote", "get-url", "origin"])).trim();
    return remote || null;
  } catch {
    return null;
  }
}

export async function pullRequestUrl(root: string): Promise<string> {
  const current = await status(root);
  if (!current.branch) throw new GitError("HEAD is detached: create a branch first");
  let remote: string;
  try {
    remote = (await git(root, ["remote", "get-url", "origin"])).trim();
  } catch {
    throw new GitError("this repository has no remote named origin");
  }
  const page = pullRequestPage(remote, current.branch, await baseBranch(root));
  if (!page) throw new GitError(`no pull-request page is known for ${remote}`);
  return page;
}

/** A detached child for an editor: the window does not wait for it or keep it. */
export function launch(command: string, args: string[], cwd: string): void {
  const child = spawn(command, args, { cwd, detached: true, stdio: "ignore", env: process.env });
  child.on("error", () => {});
  child.unref();
}

/** The local branches, the current one first, for a worktree's base. */
export async function branches(root: string): Promise<{ current: string | null; branches: string[] }> {
  const text = await git(root, ["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads/"]);
  const current = (await status(root)).branch;
  const list = text.split("\n").map((line) => line.trim()).filter(Boolean).slice(0, 100);
  return { current, branches: current ? [current, ...list.filter((name) => name !== current)] : list };
}
