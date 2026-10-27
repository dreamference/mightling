// Summaries of finished chats (DESKTOP §16 B43, DESKTOP_PARITY §9.5 decision 8): a thread left alone
// for a day gets a two-line summary from the model this machine serves, asked in a read-only thread
// that is not kept (turns.ts `askOnce`), and only while the model server has nothing else to do, the
// test suggested prompts use (no thread this window knows has a turn running). The summary shows under
// the thread's title on hover and in Search threads, and a search finds a thread by its summary.
//
// The summaries are a window preference (`threadSummaries` in prefs.json): one per thread, stamped
// with the thread's `updatedAt` when it was written, so a thread that changes is summarized again
// (once it has been idle a day again) and its old summary is no longer shown. Bounded to
// `SUMMARIES_MAX`, the summaries of the threads updated longest ago dropped first. Settings ›
// Suggested prompts turns it off; on by default.

import { isContextText } from "./projects";
import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";
import type { RpcClient } from "./rpc";
import type { ThreadView } from "./store";
import { askOnce, type Listen } from "./turns";

/** A thread is summarized once nothing happened in it for this long (seconds). */
export const IDLE_SECONDS = 24 * 60 * 60;
/** At most this many summaries are kept, and only the most recently updated idle threads get one. */
export const SUMMARIES_MAX = 100;
/** Each of the two lines is cut to this many characters. */
export const LINE_MAX = 160;
/** The transcript the model reads: each message cut to `EACH`, `TOTAL` in all. */
const EACH = 1500;
const TOTAL = 6000;

export const SUMMARY_PROMPT = "Below is a conversation between a user and an assistant. Summarize it in exactly two short lines: the first says what the user wanted, the second what came of it (done, found, left open). Each line at most 15 words, plain words, no bullets, no quotes, no headings. Answer with the two lines alone. Do not run any command.";

/** A thread's summary, and the thread's `updatedAt` it was written for. */
export interface Summary {
  at: number;
  text: string;
}

/** The summaries as stored: thread id to its summary. */
export type Summaries = Record<string, Summary>;

/** Only well-formed entries: a number stamp and a non-empty text. */
export function cleanSummaries(stored: unknown): Summaries {
  const result: Summaries = {};
  if (typeof stored !== "object" || stored === null || Array.isArray(stored)) return result;
  for (const [id, entry] of Object.entries(stored)) {
    if (typeof entry !== "object" || entry === null) continue;
    const { at, text } = entry as Record<string, unknown>;
    if (typeof at === "number" && Number.isFinite(at) && typeof text === "string" && text.trim()) result[id] = { at, text };
  }
  return result;
}

/** The thread's summary while it is current (the thread has not changed since), else null. */
export function summaryOf(summaries: Summaries, thread: Pick<Thread, "id" | "updatedAt">): string | null {
  const entry = Object.prototype.hasOwnProperty.call(summaries, thread.id) ? summaries[thread.id] : undefined;
  return entry && entry.at === thread.updatedAt ? entry.text : null;
}

/** The summaries with `text` as the thread's, the oldest stamps dropped beyond `SUMMARIES_MAX`. */
export function withSummary(summaries: Summaries, threadId: string, at: number, text: string): Summaries {
  const entries = Object.entries({ ...cleanSummaries(summaries), [threadId]: { at, text } });
  entries.sort((a, b) => b[1].at - a[1].at);
  return Object.fromEntries(entries.slice(0, SUMMARIES_MAX));
}

/** Whether the model server has nothing else to do, as far as this window knows: no turn running anywhere (as suggested prompts ask). */
export function serverIdle(threads: ThreadView[]): boolean {
  return !threads.some((view) => view.activeTurnId);
}

/** The key a thread's attempt is remembered by, so one that gave nothing is not asked again until it changes. */
export const attemptKey = (thread: Pick<Thread, "id" | "updatedAt">) => `${thread.id}:${thread.updatedAt}`;

/**
 * The thread to summarize next, or null: one of the user's own (not ephemeral, not a subagent's), not
 * running, idle a day, among the `SUMMARIES_MAX` most recently updated such threads, with no current
 * summary and not tried before as it is now; the most recently updated first.
 */
export function nextToSummarize(threads: ThreadView[], summaries: Summaries, nowSeconds: number, tried: ReadonlySet<string> = new Set()): Thread | null {
  const idle = threads
    .filter((view) => !view.thread.ephemeral && !view.thread.parentThreadId && !view.activeTurnId && view.thread.status.type !== "active")
    .map((view) => view.thread)
    .filter((thread) => nowSeconds - thread.updatedAt >= IDLE_SECONDS)
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, SUMMARIES_MAX);
  return idle.find((thread) => !summaryOf(summaries, thread) && !tried.has(attemptKey(thread))) ?? null;
}

/** The conversation as the model reads it: its first request, then its latest messages, oldest first, bounded. */
export function transcript(turns: Pick<Turn, "items">[]): string {
  const lines: string[] = [];
  for (const turn of turns) {
    for (const item of turn.items) {
      if (item.type === "userMessage") {
        const text = item.content.map((input) => (input.type === "text" && !isContextText(input.text) ? input.text : "")).join(" ").trim();
        if (text) lines.push(`User: ${text}`);
      } else if (item.type === "agentMessage" && item.text.trim()) {
        lines.push(`Assistant: ${item.text.trim()}`);
      }
    }
  }
  const cut = lines.map((line) => (line.length > EACH ? `${line.slice(0, EACH)} …` : line));
  // The first request says what the chat was for; keep it, and as many of the latest as fit.
  const [first, ...rest] = cut;
  if (first === undefined) return "";
  const latest = [...rest];
  while (latest.length && [first, ...latest].join("\n").length > TOTAL) latest.shift();
  return [first, ...(latest.length < rest.length ? ["…"] : []), ...latest].join("\n");
}

/** The question to the model. */
export function summaryPrompt(title: string, conversation: string): string {
  return `${SUMMARY_PROMPT}\n\nThe conversation${title.trim() ? ` "${title.trim()}"` : ""}:\n${conversation}`;
}

/** The model's answer as two lines: bullets, quotes and fences off, each line cut; null when it gave none. */
export function parseSummary(text: string): string | null {
  const lines: string[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim().replace(/^```.*$/, "").replace(/^#+\s+/, "").replace(/^(?:[-*•]|\d+[.)])\s+/, "").replace(/^["'“”]+|["'“”]+$/g, "").trim();
    if (!line) continue;
    lines.push(line.length > LINE_MAX ? `${line.slice(0, LINE_MAX - 1).trimEnd()}…` : line);
    if (lines.length === 2) break;
  }
  return lines.length ? lines.join("\n") : null;
}

/** Threads whose current summary holds `term` (any case), for Search threads. */
export function summaryMatches(summaries: Summaries, threads: Thread[], term: string): Thread[] {
  const wanted = term.trim().toLowerCase();
  if (!wanted) return [];
  return threads.filter((thread) => summaryOf(summaries, thread)?.toLowerCase().includes(wanted));
}

/**
 * A thread's summary by the served model: the thread read (`thread/read`, never resumed, so it is not
 * marked as active), then one turn in a read-only thread that is not kept. Null when the thread has
 * nothing to summarize or the model gave nothing.
 */
export async function summarize(client: RpcClient, listen: Listen, thread: Thread, model: string | null): Promise<string | null> {
  const read = await client.request("thread/read", { threadId: thread.id, includeTurns: true });
  const conversation = transcript(read.thread.turns ?? []);
  if (!conversation) return null;
  const title = thread.name || thread.preview || "";
  const answer = await askOnce(client, listen, { cwd: thread.cwd, model, prompt: summaryPrompt(title, conversation), timeoutMs: 120_000 });
  return parseSummary(answer);
}
