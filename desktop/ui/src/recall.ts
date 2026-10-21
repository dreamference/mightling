// Earlier questions for the agent (DESKTOP_PARITY_CLAUDE §4.4): what is kept of an Ask question, as the
// Markdown file the agent searches (desktop/electron/src/recall.ts keeps it). The question's title, when
// it was asked, the folder it worked in, then each turn's question and answer; commands, diffs and
// reasoning are left out. Pure, tested without a window.

import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";
import type { UserInput } from "./protocol/v2/UserInput";

/** Which threads are kept: Ask questions of the user's own, never an incognito (ephemeral) one or a subagent's. */
export function keptQuestions(threads: Thread[], isAsk: (thread: Thread) => boolean): Thread[] {
  return threads.filter((thread) => isAsk(thread) && !thread.ephemeral && !thread.parentThreadId);
}

const inputText = (input: UserInput): string => {
  if (input.type === "text") return input.text;
  if (input.type === "localImage") return `[image: ${input.path.split(/[\\/]/).pop()}]`;
  return "";
};

const day = (seconds: number) => new Date(seconds * 1000).toISOString().slice(0, 16).replace("T", " ");

/** The Markdown kept of one question, its turns oldest first. */
export function transcript(thread: Pick<Thread, "id" | "name" | "preview" | "cwd" | "createdAt" | "updatedAt">, turns: Turn[]): string {
  const title = (thread.name || thread.preview || "A question").replace(/\s+/g, " ").trim().slice(0, 200);
  const lines = [`# ${title}`, "", `Asked ${day(thread.createdAt)} UTC, last answered ${day(thread.updatedAt)} UTC. Folder: ${thread.cwd}`, ""];
  for (const turn of turns) {
    for (const item of turn.items) {
      if (item.type === "userMessage") {
        const text = item.content.map(inputText).filter(Boolean).join("\n").trim();
        if (text) lines.push("## Question", "", text, "");
      } else if (item.type === "agentMessage" && item.text.trim()) {
        lines.push("## Answer", "", item.text.trim(), "");
      }
    }
  }
  return `${lines.join("\n").trimEnd()}\n`;
}
