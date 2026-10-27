// The description Create pull request starts from (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B32,
// the PR-description instruction B22 left to it): written by the served model in a throwaway,
// read-only thread, as a commit message is, from the branch's changes and the instruction in
// Settings › Git (empty: PR_PROMPT). Nothing leaves the machine until the user creates the request.

import type { RpcClient } from "./rpc";
import { askOnce, cleanMessage, type Listen } from "./turns";

export const PR_PROMPT = "Write the description of a pull request for the changes below, in Markdown: what changes and why, in a few sentences or a short list, then how it was tested if the changes show it. Answer with the description alone: no title, no fences around it, no commentary.";

/** A pull request's description from the branch's diff, by the served model; the diff is cut to keep the question small. */
export async function pullRequestDescription(client: RpcClient, listen: Listen, root: string, model: string | null, diff: string, title: string, prompt: string = PR_PROMPT): Promise<string> {
  const cut = diff.length > 40_000 ? `${diff.slice(0, 40_000)}\n… (the rest of the diff is left out)` : diff;
  const named = title.trim() ? `The pull request's title: ${title.trim()}\n\n` : "";
  return cleanMessage(await askOnce(client, listen, { cwd: root, model, prompt: `${prompt}\n\n${named}${cut}` }));
}
