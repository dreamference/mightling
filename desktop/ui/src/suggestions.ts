// Suggested prompts (DESKTOP_PARITY §2.15, Phase 3 item 7): when a turn ends, the served model is
// asked, in a read-only thread that is not kept (turns.ts `askOnce`), for three short follow-ups the
// user might send next; they show above the composer and a click puts one in it, never sends it.
// Off by default: each is one more turn on the one model server.

import { askOnce, type Listen } from "./turns";
import type { RpcClient } from "./rpc";

export const SUGGEST_PROMPT = "Below are the last request a user made to a coding agent and the agent's answer. Suggest three short follow-up requests the user is likely to send next, each a single imperative sentence of at most 12 words, specific to this work. Answer with the three requests alone, one per line: no numbering, no quotes, no commentary. Do not run any command.";

/** The question to the model: the request and the answer, each cut to keep it small. */
export function suggestionPrompt(request: string, answer: string): string {
  const cut = (text: string, size: number) => (text.length > size ? `${text.slice(0, size)} …` : text);
  return `${SUGGEST_PROMPT}\n\nThe request:\n${cut(request.trim(), 2_000)}\n\nThe answer:\n${cut(answer.trim(), 4_000)}`;
}

/** The model's lines as suggestions: bullets, numbers and quotes off, empty and overlong ones out, three at most. */
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

/** Three follow-ups for a finished turn, by the served model. */
export async function suggest(client: RpcClient, listen: Listen, options: { cwd: string; model: string | null; request: string; answer: string }): Promise<string[]> {
  const text = await askOnce(client, listen, { cwd: options.cwd, model: options.model, prompt: suggestionPrompt(options.request, options.answer), timeoutMs: 120_000 });
  return parseSuggestions(text);
}
