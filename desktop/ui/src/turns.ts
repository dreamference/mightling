// What the composer asks of the server (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.3), as
// functions over the JSON-RPC client so a scripted stand-in can drive them (composer.test.ts): a
// message starts a turn, or, while one runs, steers it or waits in the thread's queue (the server
// starts the next queued message itself when the turn ends); Esc Esc takes the last message back;
// a fork copies the thread. Every method here is in ling-rs/web/policy.json.

import type { RpcClient } from "./rpc";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { QueuedSubmission } from "./protocol/v2/QueuedSubmission";
import type { Thread } from "./protocol/v2/Thread";
import type { UserInput } from "./protocol/v2/UserInput";
import { lastUserTurn } from "./composer";
import type { ThreadView } from "./store";

export interface SendOptions {
  threadId: string;
  /** The turn running now, if any. */
  activeTurnId: string | null;
  input: UserInput[];
  /** The window's follow-up preference, for a message sent while a turn runs. */
  followUp: "steer" | "queue";
  permissions?: string | null;
  effort?: string | null;
  model?: string | null;
  /** The folder the turn runs in, when it is not the thread's own (a hand-off, DESKTOP_PARITY §2.6). */
  cwd?: string | null;
}

export type Sent = "started" | "steered" | "queued";

/** A random id for a queued message, as `thread/queue/add` asks of its client. */
export function clientMessageId(): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
  return random;
}

export async function sendMessage(client: RpcClient, options: SendOptions): Promise<Sent> {
  const { threadId, activeTurnId, input } = options;
  if (activeTurnId) {
    if (options.followUp === "queue") {
      await client.request("thread/queue/add", { threadId, input, clientUserMessageId: clientMessageId() });
      return "queued";
    }
    await client.request("turn/steer", { threadId, input, expectedTurnId: activeTurnId });
    return "steered";
  }
  await client.request("turn/start", {
    threadId,
    input,
    ...(options.permissions ? { permissions: options.permissions } : {}),
    ...(options.effort ? { effort: options.effort } : {}),
    ...(options.model ? { model: options.model } : {}),
    ...(options.cwd ? { cwd: options.cwd } : {}),
  });
  return "started";
}

/** The thread's queued messages, oldest first. */
export async function listQueue(client: RpcClient, threadId: string): Promise<QueuedSubmission[]> {
  const answer = (await client.request("thread/queue/list", { threadId })) as { data?: QueuedSubmission[] };
  return answer.data ?? [];
}

/** Sends a queued message into the running turn now, and takes it out of the queue. */
export async function steerQueued(client: RpcClient, threadId: string, activeTurnId: string | null, item: QueuedSubmission): Promise<void> {
  if (activeTurnId) await client.request("turn/steer", { threadId, input: item.input, expectedTurnId: activeTurnId });
  else await client.request("thread/queue/start", { threadId, queuedSubmissionId: item.id });
  if (activeTurnId) await client.request("thread/queue/delete", { threadId, queuedSubmissionId: item.id });
}

export async function dropQueued(client: RpcClient, threadId: string, item: QueuedSubmission): Promise<void> {
  await client.request("thread/queue/delete", { threadId, queuedSubmissionId: item.id });
}

/**
 * Takes the last message back (Esc Esc, `/edit`): the thread is reverted to before that turn, which
 * also undoes what the turn changed, and its text comes back for editing. Null when there is nothing
 * to take back or a turn is running.
 */
export async function takeBack(client: RpcClient, view: ThreadView): Promise<string | null> {
  if (view.activeTurnId) return null;
  const last = lastUserTurn(view);
  if (!last) return null;
  await client.request("thread/revert", { threadId: view.thread.id, beforeTurnId: last.turnId });
  return last.text;
}

/** A copy of the thread to continue in; the original stays as it is. */
export async function forkThread(client: RpcClient, threadId: string, model: string | null): Promise<Thread> {
  const answer = (await client.request("thread/fork", { threadId, model })) as { thread: Thread };
  return answer.thread;
}

/** Subscribes to the server's notifications; returns the unsubscribe. */
export type Listen = (listener: (notification: ServerNotification) => void) => () => void;

/**
 * One question in a throwaway thread (`ephemeral`, read-only, never asking for approval), answered
 * by the served model, for the window's own small jobs such as a commit message. Resolves with the
 * last thing the agent said.
 */
export async function askOnce(client: RpcClient, listen: Listen, options: { cwd: string; model: string | null; prompt: string; timeoutMs?: number }): Promise<string> {
  const started = await client.request("thread/start", { cwd: options.cwd, model: options.model, ephemeral: true, sandbox: "read-only", approvalPolicy: "never" });
  const threadId = started.thread.id;
  let said = "";
  let stop: () => void = () => {};
  const answered = new Promise<string>((resolve, reject) => {
    const timer = setTimeout(() => {
      stop();
      reject(new Error("the model did not answer in time"));
    }, options.timeoutMs ?? 300_000);
    stop = listen((notification) => {
      if (notification.method === "item/completed" && notification.params.threadId === threadId && notification.params.item.type === "agentMessage") {
        said = notification.params.item.text;
      } else if (notification.method === "turn/completed" && notification.params.threadId === threadId) {
        clearTimeout(timer);
        stop();
        const turn = notification.params.turn;
        if (turn.error) return reject(new Error(turn.error.message));
        const last = [...turn.items].reverse().find((item) => item.type === "agentMessage");
        resolve(said || (last && last.type === "agentMessage" ? last.text : ""));
      }
    });
  });
  try {
    await client.request("turn/start", { threadId, input: [{ type: "text", text: options.prompt, text_elements: [] }] });
    return (await answered).trim();
  } finally {
    stop();
    client.request("thread/unsubscribe", { threadId }).catch(() => {});
  }
}

/** The instruction a generated commit message starts from (Settings › Git may replace it). */
export const COMMIT_PROMPT = "Write a git commit message for the staged changes below. The first line sums the change up in the imperative mood in at most 72 characters; if the change needs explaining, add a blank line and a short body that says why. Answer with the message alone: no quotes, no code fences, no commentary.";

/** A commit message from the diff, by the served model; the diff is cut to keep the question small. */
export async function commitMessage(client: RpcClient, listen: Listen, root: string, model: string | null, diff: string, prompt: string = COMMIT_PROMPT): Promise<string> {
  const cut = diff.length > 40_000 ? `${diff.slice(0, 40_000)}\n… (the rest of the diff is left out)` : diff;
  const answer = await askOnce(client, listen, { cwd: root, model, prompt: `${prompt}\n\n${cut}` });
  return cleanMessage(answer);
}

/** A model's commit message without the fences or quotes it was asked to leave out. */
export function cleanMessage(text: string): string {
  return text.trim().replace(/^```[a-z]*\n?/i, "").replace(/\n?```$/, "").replace(/^"([\s\S]*)"$/, "$1").trim();
}
