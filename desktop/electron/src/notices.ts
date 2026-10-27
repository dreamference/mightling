// Desktop notifications (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.1, §2.15; DESKTOP §16 B1):
// which of the server's messages become a notification under the window's preferences: a turn that
// completed (each thread's own, never a throwaway thread's such as a commit message's), an approval or
// a question waiting. Each says which thread: its name (or its first words) as the title, the turn's
// last message or what waits as the body. A click brings the window forward on that thread. No Electron here.

export interface NoticePrefs {
  notifyTurns?: unknown;
  notifyApprovals?: unknown;
  /** Questions have their own switch; null (the default) follows `notifyApprovals`, the one switch there was. */
  notifyQuestions?: unknown;
}

export interface Notice {
  title: string;
  body: string;
  threadId: string | null;
  /** Shown even when the window is in front. */
  always: boolean;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

/** The longest title and body a notification gets; a desktop cuts longer ones anyway, unevenly. */
export const TITLE_CAP = 60;
export const BODY_CAP = 160;

/** `text` on one line, its whitespace folded, cut to `cap` characters with an ellipsis. */
export function oneLine(text: string, cap: number): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > cap ? `${line.slice(0, cap - 1).trimEnd()}…` : line;
}

/** A thread's name as the sidebar shows it: its given name, else its first words. */
function threadLabel(thread: Json): string | null {
  const name = typeof thread.name === "string" ? thread.name.trim() : "";
  if (name) return name;
  const preview = typeof thread.preview === "string" ? thread.preview.trim() : "";
  return preview || null;
}

const APPROVAL = /requestApproval$|^execCommandApproval$|^applyPatchApproval$/;

export class Notices {
  /** Throwaway threads (`ephemeral`) and subagents, seen as they open: never notified. */
  private readonly ephemeral = new Set<string>();
  /** Each thread's name or first words, from every answer and notification that carries a thread. */
  private readonly names = new Map<string, string>();
  /** The last message of each thread's running turn. */
  private readonly lastMessage = new Map<string, string>();

  private learn(thread: unknown): void {
    if (!isObject(thread) || typeof thread.id !== "string") return;
    const label = threadLabel(thread);
    if (label) this.names.set(thread.id, label);
  }

  private title(threadId: string | null): string {
    const name = threadId ? this.names.get(threadId) : undefined;
    return name ? oneLine(name, TITLE_CAP) : "Mightling";
  }

  /**
   * Reads one message from the server; returns the notification it calls for, if any. `prefs` is
   * read only for a message that may notify, since this runs for every streamed delta.
   */
  take(message: unknown, readPrefs: () => NoticePrefs): Notice | null {
    if (!isObject(message)) return null;
    const result = isObject(message.result) ? message.result : null;
    const opened = result && isObject(result.thread) ? result.thread : null;
    if (opened && opened.ephemeral === true && typeof opened.id === "string") this.ephemeral.add(opened.id);
    if (opened) this.learn(opened);
    if (result && Array.isArray(result.data)) for (const thread of result.data) this.learn(thread);
    const method = typeof message.method === "string" ? message.method : null;
    const params = isObject(message.params) ? message.params : {};
    // A subagent's turns are its parent's business: only the parent's end is announced.
    if (method === "thread/started" && isObject(params.thread)) {
      const started = params.thread;
      if ((started.ephemeral === true || typeof started.parentThreadId === "string") && typeof started.id === "string") this.ephemeral.add(started.id);
      this.learn(started);
    }
    const threadId = typeof params.threadId === "string" ? params.threadId : null;
    if (method === "thread/name/updated" && threadId) {
      const name = typeof params.threadName === "string" ? params.threadName.trim() : "";
      if (name) this.names.set(threadId, name);
      return null;
    }
    if (method === "turn/started" && threadId) {
      this.lastMessage.delete(threadId);
      return null;
    }
    // A new thread has no name and no preview yet: its first message stands in, as the sidebar's preview will.
    if (method === "item/completed" && threadId && isObject(params.item) && params.item.type === "userMessage" && !this.names.has(threadId)) {
      const first = Array.isArray(params.item.content) ? params.item.content.find((part) => isObject(part) && part.type === "text" && typeof part.text === "string") : undefined;
      if (isObject(first) && typeof first.text === "string" && first.text.trim()) this.names.set(threadId, first.text.trim());
      return null;
    }
    if (method === "item/completed" && threadId && isObject(params.item) && params.item.type === "agentMessage" && typeof params.item.text === "string") {
      if (params.item.text.trim()) this.lastMessage.set(threadId, params.item.text);
      return null;
    }
    if (method === "turn/completed" && threadId && !this.ephemeral.has(threadId)) {
      const last = this.lastMessage.get(threadId);
      this.lastMessage.delete(threadId);
      const prefs = readPrefs();
      if (prefs.notifyTurns === "never") return null;
      const turn = isObject(params.turn) ? params.turn : {};
      const status = turn.status === "failed" ? "stopped with an error" : turn.status === "interrupted" ? "was stopped" : "finished";
      const body = status === "finished" && last ? oneLine(last, BODY_CAP) : `The turn ${status}.`;
      return { title: this.title(threadId), body, threadId, always: prefs.notifyTurns === "always" };
    }
    if (method && "id" in message && (APPROVAL.test(method) || /requestUserInput$/.test(method) || method === "mcpServer/elicitation/request")) {
      if (method === "mcpServer/elicitation/request" && params.mode === "openai/userVerification") return null;
      const prefs = readPrefs();
      const question = /requestUserInput$/.test(method) || method === "mcpServer/elicitation/request";
      const allowed = question ? (typeof prefs.notifyQuestions === "boolean" ? prefs.notifyQuestions : prefs.notifyApprovals !== false) : prefs.notifyApprovals !== false;
      if (!allowed) return null;
      return { title: this.title(threadId), body: oneLine(waitingFor(method, params), BODY_CAP), threadId, always: false };
    }
    return null;
  }
}

/** What the agent waits for, in words: the command, the question, the MCP server's message. */
function waitingFor(method: string, params: Json): string {
  const text = (value: unknown): string | null => (typeof value === "string" && value.trim() ? value.trim() : null);
  if (method === "item/commandExecution/requestApproval" || method === "execCommandApproval") {
    const command = Array.isArray(params.command) ? params.command.filter((part) => typeof part === "string").join(" ") : text(params.command);
    return command ? `Run ${command}?` : "The agent is waiting for an approval.";
  }
  if (method === "item/fileChange/requestApproval" || method === "applyPatchApproval") {
    const reason = text(params.reason);
    return reason ? `Change files? ${reason}` : "The agent wants to change files.";
  }
  if (method === "item/permissions/requestApproval") {
    const reason = text(params.reason);
    return reason ? `More permissions? ${reason}` : "The agent asks for more permissions.";
  }
  if (/requestUserInput$/.test(method)) {
    const first = Array.isArray(params.questions) && isObject(params.questions[0]) ? text(params.questions[0].question) : null;
    return first ?? "The agent has a question.";
  }
  const serverName = text(params.serverName);
  const message = text(params.message);
  if (message) return serverName ? `${serverName}: ${message}` : message;
  return "The agent is waiting for an answer to an MCP server's question.";
}
