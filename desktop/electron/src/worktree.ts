// Managed worktrees and hand-off (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.6). The Codex
// app keeps a chat's worktree under `$CODEX_HOME/worktrees/<4 hex>/<repository>`, detached, with
// the owning thread in `codex-thread.json` beside the worktree's git metadata; the `codex-worktree`
// crate (codex-rs/worktree, pinned) writes the same contract for the TUI. Until `ling worktree`
// exposes that crate (§1.5, which needs Rust), this module follows its steps exactly with `git`
// from the main process, so the TUI's `/worktree` and the app agree on every directory:
// `worktree add --detach --no-checkout`, `core.worktree` in the worktree's own config, `reset --hard
// --no-recurse-submodules`, the owner record written once. Beyond the crate: the source's
// uncommitted changes applied when asked, `.worktreeinclude` and `AGENTS.override.md` copied, a
// snapshot (a commit kept under refs/mightling/snapshots/) before any removal or hand-off, and the
// hand-off itself. No Electron here.

import crypto from "node:crypto";
import fsp from "node:fs/promises";
import path from "node:path";

import { GitError, git } from "./git";
import * as userFs from "./userfs";

export interface ManagedWorktree {
  root: string;
  cwd: string;
  sourceRoot: string;
  sourceCwd: string;
  headSha: string;
  branch: string | null;
}

const OWNER_FILE = "codex-thread.json";
const SNAPSHOTS = "refs/mightling/snapshots";

/** The default worktree root, `$CODEX_HOME/worktrees` (the crate's `WorktreeSettings` default). */
export function defaultRoot(codexHome: string): string {
  return path.join(codexHome, "worktrees");
}

/** A worktree root from settings: absolute or refused, as the crate requires. */
export function checkWorktreeRoot(root: unknown): string {
  if (typeof root !== "string" || !path.isAbsolute(root)) throw new GitError("the worktree root must be an absolute path");
  return path.resolve(root);
}

/** Whether `checkout` has the managed shape `<root>/<4 hex>/<name>` (the crate's `is_managed_root`). */
export function isManaged(root: string, checkout: string): boolean {
  const relative = path.relative(root, checkout);
  const parts = relative.split(path.sep);
  return parts.length === 2 && /^[0-9a-f]{4}$/.test(parts[0]) && parts[1] !== "" && !relative.startsWith("..");
}

/** A new bucket under `root` for `name`: the crate's `allocate_worktree_root`. */
async function allocate(root: string, name: string): Promise<string> {
  await fsp.mkdir(root, { recursive: true });
  for (let attempt = 0; attempt < 65536; attempt++) {
    const bucket = path.join(root, crypto.randomUUID().replace(/-/g, "").slice(0, 4));
    try {
      await fsp.mkdir(bucket);
      return path.join(bucket, name);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
  }
  throw new GitError("all managed worktree identifiers are in use");
}

const removeEmptyBucket = async (checkout: string) => {
  try {
    await fsp.rmdir(path.dirname(checkout));
  } catch {
    // Not empty, or gone already.
  }
};

const top = async (cwd: string) => userFs.realPath((await git(cwd, ["rev-parse", "--show-toplevel"])).trim());

/** The ignored files `.worktreeinclude` names (and an ignored `AGENTS.override.md`), relative to the root. */
export async function includedIgnoredFiles(sourceRoot: string): Promise<string[]> {
  const ignored = new Set((await git(sourceRoot, ["ls-files", "-z", "--others", "--ignored", "--exclude-standard"])).split("\0").filter(Boolean));
  const wanted = new Set<string>();
  if (await userFs.exists(path.join(sourceRoot, ".worktreeinclude"))) {
    const listed = await git(sourceRoot, ["ls-files", "-z", "--others", "--ignored", `--exclude-from=${path.join(sourceRoot, ".worktreeinclude")}`]);
    for (const file of listed.split("\0").filter(Boolean)) if (ignored.has(file)) wanted.add(file);
  }
  for (const file of ignored) if (path.basename(file) === "AGENTS.override.md") wanted.add(file);
  return [...wanted].sort().slice(0, 2000);
}

/** Copies files into a new checkout: never a symlink, never over a file that is there. */
export async function copyInto(sourceRoot: string, checkout: string, files: string[]): Promise<string[]> {
  const copied: string[] = [];
  for (const file of files) {
    const from = path.join(sourceRoot, file);
    const to = path.join(checkout, file);
    const stat = await userFs.lstatOrNull(from);
    if (!stat || !stat.isFile() || stat.isSymbolicLink() || (await userFs.exists(to))) continue;
    await userFs.mkdir(path.dirname(to));
    await userFs.copyFile(from, to);
    copied.push(file);
  }
  return copied;
}

/**
 * A commit of everything in `checkout` that git does not ignore (tracked changes and untracked
 * files), on top of its HEAD, kept under refs/mightling/snapshots/<name>; HEAD, the index and the
 * work tree are left as they were. Null when there is nothing beyond HEAD.
 */
export async function snapshot(checkout: string, name: string): Promise<string | null> {
  const head = (await git(checkout, ["rev-parse", "HEAD"])).trim();
  const gitDir = (await git(checkout, ["rev-parse", "--absolute-git-dir"])).trim();
  const index = path.join(gitDir, `mightling-snapshot-${process.pid}-${Date.now()}.index`);
  const env = { GIT_INDEX_FILE: index };
  try {
    await gitWith(checkout, env, ["read-tree", "HEAD"]);
    await gitWith(checkout, env, ["add", "-A"]);
    const tree = (await gitWith(checkout, env, ["write-tree"])).trim();
    const headTree = (await git(checkout, ["rev-parse", "HEAD^{tree}"])).trim();
    if (tree === headTree) return null;
    const commit = (await git(checkout, ["commit-tree", tree, "-p", head, "-m", `Mightling snapshot of ${checkout}`])).trim();
    await git(checkout, ["update-ref", `${SNAPSHOTS}/${name.replace(/[^A-Za-z0-9._-]/g, "-")}`, commit]);
    return commit;
  } finally {
    await fsp.rm(index, { force: true });
  }
}

/** `git` with a private index. */
const gitWith = (cwd: string, env: Record<string, string>, args: string[]) => git(cwd, args, undefined, 60_000, env);

/** Applies `snapshotSha`'s changes against its parent to `checkout`'s work tree (the index untouched). */
async function applySnapshot(checkout: string, snapshotSha: string): Promise<void> {
  const patch = await git(checkout, ["diff", "--binary", `${snapshotSha}^`, snapshotSha]);
  if (patch.trim()) await git(checkout, ["apply", "--binary", "--whitespace=nowarn", "-"], patch);
}

export interface CreateOptions {
  sourceCwd: string;
  /** The commit to start from: a branch or sha; null for the source's HEAD. */
  base: string | null;
  /** Apply the source's uncommitted changes (only with the current HEAD as the base). */
  withChanges: boolean;
  worktreeRoot: string;
}

export interface Created extends ManagedWorktree {
  /** Ignored files copied in by `.worktreeinclude`. */
  included: string[];
}

/** Creates a managed worktree: the crate's `WorktreeManager::create`, then the extras above. */
export async function create(options: CreateOptions): Promise<Created> {
  const worktreeRoot = checkWorktreeRoot(options.worktreeRoot);
  if (!path.isAbsolute(options.sourceCwd)) throw new GitError("the project folder must be an absolute path");
  const sourceCwd = await userFs.realPath(options.sourceCwd);
  const sourceRoot = await top(sourceCwd);
  const relative = path.relative(sourceRoot, sourceCwd);
  if (relative.startsWith("..")) throw new GitError("the working directory is outside the repository root");
  const base = options.base && /^[\w./@^~-]+$/.test(options.base) && !options.base.startsWith("-") ? options.base : "HEAD";
  const headSha = (await git(sourceRoot, ["rev-parse", "--verify", "--end-of-options", `${base}^{commit}`])).trim();
  const root = await allocate(worktreeRoot, path.basename(sourceRoot));
  try {
    await git(sourceRoot, ["worktree", "add", "--detach", "--no-checkout", root, headSha]);
  } catch (error) {
    await removeEmptyBucket(root);
    throw new GitError(`cannot create managed worktree: ${(error as Error).message}`);
  }
  let included: string[];
  try {
    const config = (await git(root, ["rev-parse", "--path-format=absolute", "--git-path", "config.worktree"])).trim();
    await git(root, ["config", "--file", config, "core.worktree", root]);
    await git(root, ["--work-tree=.", "reset", "--hard", "--no-recurse-submodules", headSha]);
    if (options.withChanges && base === "HEAD") {
      const changes = await snapshot(sourceRoot, `source-${path.basename(path.dirname(root))}`);
      if (changes) await applySnapshot(root, changes);
    }
    included = await copyInto(sourceRoot, root, await includedIgnoredFiles(sourceRoot));
  } catch (error) {
    await removeChecked(sourceRoot, root, null).catch(() => {});
    throw new GitError(`cannot populate managed worktree: ${(error as Error).message}`);
  }
  const cwd = path.join(root, relative);
  if (!(await userFs.exists(cwd))) {
    await removeChecked(sourceRoot, root, null).catch(() => {});
    throw new GitError(`the base does not contain the working directory ${relative}`);
  }
  return { root, cwd, sourceRoot, sourceCwd, headSha, branch: null, included };
}

/** The thread a worktree belongs to, from `codex-thread.json` (the crate's `owner`). */
export async function owner(checkout: string): Promise<string | null> {
  const file = path.resolve(checkout, (await git(checkout, ["rev-parse", "--git-path", OWNER_FILE])).trim());
  let record: { version?: unknown; ownerThreadId?: unknown };
  try {
    record = JSON.parse(await userFs.readText(file));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw new GitError(`invalid worktree owner at ${file}`);
  }
  if (record.version !== 1 || typeof record.ownerThreadId !== "string" || !record.ownerThreadId) throw new GitError(`invalid worktree owner at ${file}`);
  return record.ownerThreadId;
}

/** Binds a worktree to its thread once (the crate's `bind_thread`): the same thread again is a no-op, another is refused. */
export async function bindThread(checkout: string, threadId: string): Promise<void> {
  if (!threadId) throw new GitError("worktree owner thread ID cannot be empty");
  const existing = await owner(checkout);
  if (existing === threadId) return;
  if (existing) throw new GitError(`worktree already belongs to thread ${existing}`);
  const file = path.resolve(checkout, (await git(checkout, ["rev-parse", "--git-path", OWNER_FILE])).trim());
  // One name per bind, not per process: two binds at once (two windows) must not write each other's file.
  const temporary = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
  await fsp.writeFile(temporary, JSON.stringify({ version: 1, ownerThreadId: threadId }));
  try {
    await fsp.link(temporary, file);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST" && (await owner(checkout)) === threadId) return;
    throw new GitError("worktree was concurrently assigned to another thread");
  } finally {
    await fsp.rm(temporary, { force: true });
  }
}

export interface Listed extends ManagedWorktree {
  owner: string | null;
  /** When the checkout last changed, for the cleanup's "keep the most recent". */
  modified: number;
}

/** The source repository's managed worktrees under `worktreeRoot` (the crate's `list`). */
export async function list(sourceCwd: string, worktreeRoot: string): Promise<Listed[]> {
  const sourceRoot = await top(sourceCwd);
  // Compared as the file system has them: git lists long names, and the root may be an 8.3 short name on Windows.
  const rootDir = await userFs.resolvedPath(checkWorktreeRoot(worktreeRoot));
  const text = await git(sourceRoot, ["worktree", "list", "--porcelain", "-z"]);
  const found: Listed[] = [];
  for (const record of text.split("\0\0")) {
    const fields = record.split("\0").filter(Boolean);
    const listed = fields.find((field) => field.startsWith("worktree "))?.slice(9);
    if (!listed || !(await userFs.exists(listed))) continue;
    const checkout = await userFs.realPath(listed);
    if (!isManaged(rootDir, checkout)) continue;
    const head = fields.find((field) => field.startsWith("HEAD "))?.slice(5) ?? "";
    const branch = fields.find((field) => field.startsWith("branch "))?.slice(7).replace(/^refs\/heads\//, "") ?? null;
    found.push({
      root: checkout, cwd: checkout, sourceRoot, sourceCwd: sourceRoot, headSha: head, branch,
      owner: await owner(checkout).catch(() => null), modified: (await userFs.statOrNull(checkout))?.mtimeMs ?? 0,
    });
  }
  return found.sort((a, b) => a.root.localeCompare(b.root));
}

/** Checks that `checkout` is one of `sourceCwd`'s managed worktrees under `worktreeRoot`, and returns the source's root. */
export async function checkManaged(sourceCwd: string, checkout: unknown, worktreeRoot: string): Promise<string> {
  if (typeof checkout !== "string" || !isManaged(await userFs.resolvedPath(checkWorktreeRoot(worktreeRoot)), await userFs.resolvedPath(checkout))) throw new GitError("not a managed worktree");
  const listed = await list(sourceCwd, worktreeRoot);
  const wanted = await userFs.pathKey(checkout);
  let match: Listed | undefined;
  for (const entry of listed) if ((await userFs.pathKey(entry.root)) === wanted) match = entry;
  if (!match) throw new GitError(`${checkout} is not a worktree of ${sourceCwd}`);
  return match.sourceRoot;
}

/**
 * Removes a managed worktree of `sourceCwd` after a snapshot of its work (null when it had none);
 * `name` names the snapshot. Anything that is not one of the source's managed worktrees is refused.
 */
export async function remove(sourceCwd: string, checkout: string, worktreeRoot: string, name: string | null): Promise<string | null> {
  const sourceRoot = await checkManaged(sourceCwd, checkout, worktreeRoot);
  return removeChecked(sourceRoot, checkout, name);
}

async function removeChecked(sourceRoot: string, checkout: string, name: string | null): Promise<string | null> {
  let saved: string | null = null;
  // A snapshot that fails keeps the worktree: removing it then would lose the work it was to save.
  if (name && (await userFs.exists(checkout))) saved = await snapshot(checkout, name);
  try {
    await git(sourceRoot, ["worktree", "remove", "--force", checkout]);
  } catch {
    await git(sourceRoot, ["worktree", "remove", "--force", "--force", checkout]);
  }
  await removeEmptyBucket(checkout);
  return saved;
}

/** A new managed worktree with a snapshot's work in it: what Restore does for a thread whose worktree was removed. */
export async function restore(sourceCwd: string, snapshotSha: string, worktreeRoot: string): Promise<Created> {
  if (!/^[0-9a-f]{7,64}$/.test(snapshotSha)) throw new GitError("not a snapshot");
  const created = await create({ sourceCwd, base: `${snapshotSha}^`, withChanges: false, worktreeRoot });
  await applySnapshot(created.root, snapshotSha);
  return created;
}

/** Turns a worktree's detached HEAD into a branch ("Create branch here"). */
export async function createBranch(checkout: string, name: unknown): Promise<string> {
  if (typeof name !== "string" || !name.trim()) throw new GitError("name the branch");
  await git(checkout, ["check-ref-format", "--branch", name.trim()]);
  await git(checkout, ["switch", "--quiet", "-c", name.trim()]);
  return name.trim();
}

async function clean(checkout: string): Promise<boolean> {
  return (await git(checkout, ["status", "--porcelain", "--untracked-files=all"])).trim() === "";
}

async function branchOf(checkout: string): Promise<string | null> {
  try {
    return (await git(checkout, ["symbolic-ref", "--quiet", "--short", "HEAD"])).trim() || null;
  } catch {
    return null;
  }
}

/**
 * Hand-off (§2.6): moves a thread's work between its worktree and the local checkout. The side the
 * work leaves is snapshotted first, then cleaned (ignored files stay); the side it reaches must be
 * clean. A branch moves with the work: the worktree lets go of it before the local checkout takes
 * it (git checks a branch out in one place only); a detached worktree's commit is checked out
 * detached in the local checkout when it differs from the local HEAD.
 */
export async function handOff(direction: "toLocal" | "toWorktree", worktree: string, local: string, worktreeRoot: string, threadId: string): Promise<{ snapshot: string | null }> {
  if (typeof local !== "string" || !path.isAbsolute(local)) throw new GitError("the local checkout is named by its absolute path");
  const sourceRoot = await checkManaged(local, worktree, worktreeRoot);
  if (!(await userFs.samePath(await top(local), sourceRoot))) throw new GitError("the local checkout is not the worktree's source");
  local = sourceRoot;
  const [from, to] = direction === "toLocal" ? [worktree, local] : [local, worktree];
  if (!(await clean(to))) {
    throw new GitError(direction === "toLocal"
      ? "the local checkout has uncommitted changes: commit or stash them first"
      : "the worktree has uncommitted changes of its own");
  }
  const saved = await snapshot(from, `${threadId}-${direction}-${Date.now()}`);
  const fromHead = (await git(from, ["rev-parse", "HEAD"])).trim();
  const toHead = (await git(to, ["rev-parse", "HEAD"])).trim();
  const branch = await branchOf(from);
  if (direction === "toLocal" && branch) {
    await git(from, ["switch", "--quiet", "--detach"]);
    await git(to, ["switch", "--quiet", branch]);
  } else if (fromHead !== toHead) {
    await git(to, ["switch", "--quiet", "--detach", fromHead]);
  }
  if (saved) {
    await applySnapshot(to, saved);
    await git(from, ["reset", "--hard", "--quiet"]);
    await git(from, ["clean", "-fdq"]);
  }
  return { snapshot: saved };
}
