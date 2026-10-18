// The pull-request tab (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.5, Phase 3 item 9):
// Mightling's answer to the vendor's Code Review plugin, through the user's own GitHub CLI (`gh`)
// and its sign-in, never a token of ours. Optional and off by default (Settings › Git); refused at
// `/airgapped on` (main.ts asks first, as for push). `gh` runs with an argument list in the
// repository's top level, no prompt, no pager, no update check, its output capped.

import { execFile } from "node:child_process";

import { MAX_OUTPUT } from "./git";

export class PrError extends Error {}

/** `gh`'s environment: no prompt, no pager, no colour, no update notice (that one reaches the network on its own). */
export function ghEnv(base: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  return { ...base, GH_PROMPT_DISABLED: "1", GH_NO_UPDATE_NOTIFIER: "1", GH_SPINNER_DISABLED: "1", GH_PAGER: "cat", PAGER: "cat", NO_COLOR: "1", CLICOLOR: "0" };
}

function gh(root: string, args: string[], timeoutMs = 60_000): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(process.env.MIGHTLING_GH ?? "gh", args, { cwd: root, env: ghEnv(), maxBuffer: MAX_OUTPUT, timeout: timeoutMs, encoding: "utf8" }, (error, stdout, stderr) => {
      if (!error) return resolve(stdout);
      const code = (error as { code?: string | number }).code;
      if (code === "ENOENT") return reject(new PrError("the GitHub CLI (gh) is not installed: install it and sign in with `gh auth login`"));
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

/** Whether `gh` is there and signed in, in its own words, for Settings › Git. */
export async function status(root: string): Promise<string> {
  try {
    const out = await gh(root, ["auth", "status"], 20_000);
    return out.trim() || "Signed in.";
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}
