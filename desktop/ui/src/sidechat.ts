// A side chat that is not kept (DESKTOP §16 B37, DESKTOP_PARITY §9.5 decision 8): Ctrl+Alt+S opens a
// free chat beside the thread. It is an ephemeral thread (`ephemeral: true`, as an incognito question),
// so the server keeps no session file of it; it has no folder of its own (it may read the thread's,
// read-only, and never asks for an approval), its first message carries the thread's recap (its latest
// messages, tonight.ts), and it is gone when it is closed. **Keep as a thread** starts a saved thread
// beside the first one whose first message carries what the side chat said. Pure.

import type { Thread } from "./protocol/v2/Thread";
import type { ThreadView } from "./store";
import { NAME_MAX } from "./threads";
import { withThreadContext } from "./tonight";

/** Where the recap starts in the side chat's first message (tonight.ts `withThreadContext`). */
const RECAP = "\n\nContext: this was asked in the conversation ";

/** A kept copy's message is cut to this many characters each, and to `KEPT_TOTAL` in all. */
const KEPT_EACH = 4000;
const KEPT_TOTAL = 16000;

/** One message of the side chat, as its pane shows it. */
export interface SideMessage {
  id: string;
  role: "user" | "agent";
  text: string;
}

/** `thread/start` for a side chat: unkept, in the thread's folder read-only, never waiting for an approval. */
export function sideChatParams(parent: Thread, model: string | null): Record<string, unknown> {
  return { cwd: parent.cwd, model, ephemeral: true, sandbox: "read-only", approvalPolicy: "never" };
}

/** The side chat's first message: the user's words, then the thread's recap (none when the thread said nothing yet). */
export function firstMessage(text: string, title: string, recap: string[]): string {
  return withThreadContext(text, title, recap);
}

/** A message's words without the recap the window put after them. */
export function withoutRecap(text: string): string {
  const at = text.indexOf(RECAP);
  return at < 0 ? text : text.slice(0, at);
}

/** The side chat's messages, oldest first: the full texts (the recap included) or as shown (`shown`, without it). */
export function sideMessages(view: ThreadView | null, shown = true): SideMessage[] {
  if (!view) return [];
  const messages: SideMessage[] = [];
  for (const turn of view.turns) {
    for (const item of turn.items) {
      if (item.type === "userMessage") {
        const text = item.content.map((input) => (input.type === "text" ? input.text : "")).join(" ").trim();
        if (text) messages.push({ id: item.id, role: "user", text: shown ? withoutRecap(text) : text });
      } else if (item.type === "agentMessage" && item.text.trim()) {
        messages.push({ id: item.id, role: "agent", text: item.text.trim() });
      }
    }
  }
  return messages;
}

/** The first message of a side chat's saved copy: what was said there, oldest first, bounded (the first message, which carries the recap, always stays). */
export function keptMessage(title: string, messages: SideMessage[]): string {
  const lines = messages.map((message) => {
    const text = message.text.length > KEPT_EACH ? `${message.text.slice(0, KEPT_EACH)} …` : message.text;
    return `${message.role === "user" ? "User" : "Agent"}: ${text}`;
  });
  while (lines.join("\n\n").length > KEPT_TOTAL && lines.length > 2) lines.splice(1, 1);
  return `This conversation was a side chat beside the conversation "${title}", kept here. What was said in it, oldest first:\n\n${lines.join("\n\n")}\n\nAnswer only that it is kept; I will go on from here.`;
}

/** The name of a side chat's saved copy. */
export function keptName(title: string): string {
  return `Side chat: ${title}`.slice(0, NAME_MAX);
}
