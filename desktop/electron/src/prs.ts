// The pull-request tab (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.5, Phase 3 item 9):
// Mightling's answer to the vendor's Code Review plugin, through the user's own GitHub CLI (`gh`)
// and its sign-in, never a token of ours. Optional and off by default (Settings › Git); refused at
// `/airgapped on` (main.ts asks first, as for push). `gh` runs with an argument list in the
// repository's top level, no prompt, no pager, no update check, its output capped.

import { execFile } from "node:child_process";

import { MAX_OUTPUT } from "./git";
import { programOnPath } from "./platform";

export class PrError extends Error {}

const NO_GH = "the GitHub CLI (gh) is not installed: install it and sign in with `gh auth login`";

/** `gh`'s environment: no prompt, no pager, no colour, no update notice (that one reaches the network on its own). */
export function ghEnv(base: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  return { ...base, GH_PROMPT_DISABLED: "1", GH_NO_UPDATE_NOTIFIER: "1", GH_SPINNER_DISABLED: "1", GH_PAGER: "cat", PAGER: "cat", NO_COLOR: "1", CLICOLOR: "0" };
}

function gh(root: string, args: string[], timeoutMs = 60_000): Promise<string> {
  return new Promise((resolve, reject) => {
    // By its place on PATH, never its bare name: Node on Windows would first run a `gh.exe` the repository ships.
    const program = programOnPath(process.env.MIGHTLING_GH ?? "gh");
    if (!program) return reject(new PrError(NO_GH));
    execFile(program, args, { cwd: root, env: ghEnv(), maxBuffer: MAX_OUTPUT, timeout: timeoutMs, encoding: "utf8" }, (error, stdout, stderr) => {
      if (!error) return resolve(stdout);
      const code = (error as { code?: string | number }).code;
      if (code === "ENOENT") return reject(new PrError(NO_GH));
      reject(new PrError((stderr || error.message).trim().split("\n").slice(-3).join(" ")));
    });
  });
}

/** Which pull requests the inbox lists. */
export type Inbox = "review" | "mine" | "open";

const SEARCH: Record<Inbox, string> = { review: "review-requested:@me", mine: "author:@me", open: "" };

const LIST_FIELDS = "number,title,author,headRefName,baseRefName,isDraft,reviewDecision,updatedAt,url";
const VIEW_FIELDS = "number,title,body,author,state,isDraft,headRefName,baseRefName,additions,deletions,changedFiles,reviewDecision,statusCheckRollup,url,updatedAt";

/** A pull request's number as `gh` takes it: a positive integer, nothing else. */
export function prNumber(value: unknown): string {
  const number = Number(value);
  if (!Number.isInteger(number) || number <= 0 || number > 10_000_000) throw new PrError("not a pull request number");
  return String(number);
}

export async function list(root: string, inbox: Inbox): Promise<unknown> {
  const args = ["pr", "list", "--state", "open", "--limit", "50", "--json", LIST_FIELDS];
  const search = SEARCH[inbox];
  if (search === undefined) throw new PrError("not an inbox");
  if (search) args.push("--search", search);
  return JSON.parse(await gh(root, args) || "[]");
}

export async function view(root: string, number: unknown): Promise<unknown> {
  return JSON.parse(await gh(root, ["pr", "view", prNumber(number), "--json", VIEW_FIELDS]));
}

export async function diff(root: string, number: unknown): Promise<string> {
  return gh(root, ["pr", "diff", prNumber(number), "--color", "never"]);
}

export type Verdict = "comment" | "approve" | "request-changes";

/** A review submitted as the user, with their words; GitHub refuses an empty comment or change request. */
export async function review(root: string, number: unknown, verdict: Verdict, body: unknown): Promise<string> {
  if (!["comment", "approve", "request-changes"].includes(verdict)) throw new PrError("not a review verdict");
  const text = typeof body === "string" ? body.slice(0, 65_000) : "";
  if (verdict !== "approve" && !text.trim()) throw new PrError("a comment or a change request needs words");
  const args = ["pr", "review", prNumber(number), `--${verdict}`];
  if (text.trim()) args.push("--body", text);
  return (await gh(root, args)).trim() || "Submitted.";
}

/** A pull request's comments and reviews (DESKTOP §16 B32), as `gh pr view --json comments,reviews` answers. */
export async function activity(root: string, number: unknown): Promise<unknown> {
  return JSON.parse(await gh(root, ["pr", "view", prNumber(number), "--json", "comments,reviews"]));
}

/** A branch name as `gh` takes it for `--base`: git's characters only, never an option. */
export function branchName(value: unknown): string {
  const name = typeof value === "string" ? value.trim() : "";
  if (!/^[A-Za-z0-9._/-]{1,200}$/.test(name) || name.startsWith("-") || name.includes("..") || name.endsWith("/") || name.endsWith(".lock")) throw new PrError("not a branch name");
  return name;
}

/** What Create pull request sends: a title, a description, the base branch (none: the repository's default) and draft or not. */
export interface Draft {
  title: string;
  body: string;
  base: string | null;
  draft: boolean;
}

/**
 * Opens a pull request from the checked-out branch as the user (`gh pr create`), with their title and
 * description; never `--web` or `--fill`. Each value is joined to its flag (`--title=…`), so a title
 * that starts with a dash stays a title. With prompts off, `gh` refuses a branch it would have to push.
 */
export async function create(root: string, draft: unknown): Promise<string> {
  const given = (typeof draft === "object" && draft !== null ? draft : {}) as Partial<Record<keyof Draft, unknown>>;
  const title = typeof given.title === "string" ? given.title.replace(/[\r\n]+/g, " ").trim().slice(0, 256) : "";
  if (!title) throw new PrError("a pull request needs a title");
  const body = typeof given.body === "string" ? given.body.slice(0, 65_000) : "";
  const args = ["pr", "create", `--title=${title}`, `--body=${body}`];
  if (given.base !== null && given.base !== undefined && given.base !== "") args.push(`--base=${branchName(given.base)}`);
  if (given.draft === true) args.push("--draft");
  return (await gh(root, args, 120_000)).trim();
}

/** The tab's messages from the window, each a `gh` run that reaches GitHub. */
export type Message =
  | { type: "pr/list"; root: string; inbox: Inbox; thread?: string | null }
  | { type: "pr/view" | "pr/diff" | "pr/activity"; root: string; number: number; thread?: string | null }
  | { type: "pr/review"; root: string; number: number; verdict: Verdict; body: string; thread?: string | null }
  | { type: "pr/create"; root: string; draft: Draft; thread?: string | null };

/** What main.ts checks before any of them: Settings › Git, the thread's air-gap level (as for push), the repository. */
export interface Gate {
  enabled: boolean;
  /** Throws at `/airgapped on`: the tab reaches the network. */
  offline: (what: string) => Promise<void>;
  checkRoot: (root: unknown) => Promise<string>;
}

/** One of the tab's messages, refused before `gh` runs when the tab is off or the thread is at `/airgapped on`. */
export async function handle(message: Message, gate: Gate): Promise<unknown> {
  if (!gate.enabled) throw new PrError("the pull-request tab is off: turn it on in Settings › Git");
  await gate.offline(message.type === "pr/create" ? "creating a pull request" : "the pull-request tab");
  const root = await gate.checkRoot(message.root);
  switch (message.type) {
    case "pr/list": return list(root, message.inbox);
    case "pr/view": return view(root, message.number);
    case "pr/diff": return diff(root, message.number);
    case "pr/activity": return activity(root, message.number);
    case "pr/review": return review(root, message.number, message.verdict, message.body);
    case "pr/create": return create(root, message.draft);
  }
  throw new PrError("not a pull-request message");
}

/** Whether `gh` is there and signed in, in its own words, for Settings › Git. */
export async function status(root: string): Promise<string> {
  try {
    const out = await gh(root, ["auth", "status"], 20_000);
    return out.trim() || "Signed in.";
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}
