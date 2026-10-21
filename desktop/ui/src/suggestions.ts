// The predicted next message (DESKTOP_PARITY_CLAUDE §4.6, decided 2026-10-21; it replaced Phase 3
// item 7's three suggestion chips): when a turn ends, the served model is asked, in a read-only thread
// that is not kept (turns.ts `askOnce`), for the one message the user is most likely to send next; it
// shows in the empty composer in grey, Tab or → takes it into the composer, it is never sent by itself.
// Off by default (Settings › Suggested prompts): each is one more turn on the one model server.

import { askOnce, type Listen } from "./turns";
import type { RpcClient } from "./rpc";

export const SUGGEST_PROMPT = "Below are the last request a user made to an assistant and the assistant's answer. Write the one message the user is most likely to send next: a single sentence of at most 15 words, specific to this conversation, in the user's own voice. Answer with that message alone on one line: no numbering, no quotes, no commentary. Do not run any command.";

/** The question to the model: the request and the answer, each cut to keep it small. */
export function suggestionPrompt(request: string, answer: string): string {
  const cut = (text: string, size: number) => (text.length > size ? `${text.slice(0, size)} …` : text);
  return `${SUGGEST_PROMPT}\n\nThe request:\n${cut(request.trim(), 2_000)}\n\nThe answer:\n${cut(answer.trim(), 4_000)}`;
}

/** The model's lines as suggestions: bullets, numbers and quotes off, empty and overlong ones out, three at most (the first is the prediction). */
export function parseSuggestions(text: string): string[] {
  const seen = new Set<string>();
  const found: string[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim().replace(/^```.*$/, "").replace(/^(?:[-*•]|\d+[.)])\s+/, "").replace(/^["'“”]+|["'“”]+$/g, "").trim();
    if (!line || line.length > 160 || seen.has(line.toLowerCase())) continue;
    seen.add(line.toLowerCase());
    found.push(line);
    if (found.length === 3) break;
  }
  return found;
}

/** The predicted next message for a finished turn, by the served model; null when it gave none. */
export async function predict(client: RpcClient, listen: Listen, options: { cwd: string; model: string | null; request: string; answer: string }): Promise<string | null> {
  const text = await askOnce(client, listen, { cwd: options.cwd, model: options.model, prompt: suggestionPrompt(options.request, options.answer), timeoutMs: 120_000 });
  return parseSuggestions(text)[0] ?? null;
}

/** What a key does with a prediction in the empty composer: Tab or → (with no modifier) takes it. */
export function acceptsPrediction(event: { key: string; shiftKey: boolean; ctrlKey: boolean; altKey: boolean; metaKey: boolean }, draft: string, prediction: string | null): boolean {
  if (!prediction || draft !== "" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return false;
  return event.key === "Tab" || event.key === "ArrowRight";
}
