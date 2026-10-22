// "Do this tonight" from inside a thread (DESKTOP_PARITY §9.5, the owner's decision 7): the task goes
// to Night Shift with that thread's context, its latest messages, so the night's run knows what was
// said. Night Shift's run is a new session (best-of-N runs several), so the context travels in the
// task's words. Pure.

import { isContextText } from "./projects";
import type { ThreadView } from "./store";

/** At most this many of the latest messages, each cut to `EACH` characters, `TOTAL` in all (the task's limit is 8000). */
const MESSAGES = 8;
const EACH = 1200;
const TOTAL = 5000;

/** The thread's latest messages, oldest first, as lines a task can carry. */
export function threadContext(view: ThreadView | null): string[] {
  if (!view) return [];
  const lines: string[] = [];
  for (const turn of view.turns) {
    for (const item of turn.items) {
      if (item.type === "userMessage") {
        const text = item.content.map((input) => (input.type === "text" && !isContextText(input.text) ? input.text : "")).join(" ").trim();
        if (text) lines.push(`User: ${text}`);
      } else if (item.type === "agentMessage" && item.text.trim()) {
        lines.push(`Agent: ${item.text.trim()}`);
      }
    }
  }
  const latest = lines.slice(-MESSAGES).map((line) => (line.length > EACH ? `${line.slice(0, EACH)} …` : line));
  while (latest.join("\n").length > TOTAL && latest.length > 1) latest.shift();
  return latest;
}

/** The task's words with the thread's context after them. */
export function withThreadContext(task: string, title: string, context: string[]): string {
  if (context.length === 0) return task.trim();
  return `${task.trim()}\n\nContext: this was asked in the conversation "${title}". Its latest messages, oldest first:\n${context.join("\n")}`;
}
