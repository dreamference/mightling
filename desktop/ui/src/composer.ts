// The composer's logic (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.3), kept out of the
// component so it tests without a window: which completion menu the caret is in (`@` files, `$`
// skills, `/` commands), what choosing an entry writes back, the slash commands and what each needs,
// the prompt history ↑ walks, what Enter does, and the answers Enter and Esc give an open approval.

import type { ServerRequest } from "./protocol/ServerRequest";
import type { Model } from "./protocol/v2/Model";
import type { SkillMetadata } from "./protocol/v2/SkillMetadata";
import type { UserInput } from "./protocol/v2/UserInput";
import type { ThreadView } from "./store";

export type TriggerKind = "@" | "$" | "/";

/** The word being completed: its kind, what follows the sigil, and where it sits in the text. */
export interface Trigger {
  kind: TriggerKind;
  query: string;
  start: number;
  end: number;
}

/**
 * The completion the caret is in, if any: `@` and `$` start a word anywhere, `/` only at the very
 * start of the message (as in the TUI). The word runs from the sigil to the caret, with no space.
 */
export function activeTrigger(text: string, caret: number): Trigger | null {
  let start = caret;
  while (start > 0 && !/\s/.test(text[start - 1])) start--;
  const word = text.slice(start, caret);
  const kind = word[0] as TriggerKind | undefined;
  if (kind !== "@" && kind !== "$" && kind !== "/") return null;
  if (kind === "/" && (start !== 0 || word.slice(1).includes("/"))) return null;
  let end = caret;
  while (end < text.length && !/\s/.test(text[end])) end++;
  return { kind, query: word.slice(1), start, end };
}

/** The text with the trigger's word replaced by `insertion` and a space after it, and where the caret goes. */
export function applyCompletion(text: string, trigger: Trigger, insertion: string): { text: string; caret: number } {
  const before = text.slice(0, trigger.start);
  const after = text.slice(trigger.end).replace(/^\s*/, "");
  const inserted = `${insertion} `;
  return { text: `${before}${inserted}${after}`, caret: before.length + inserted.length };
}

/** A path as the agent should read it: relative to the thread's folder when it is inside it. */
export function mentionPath(root: string, path: string): string {
  const base = root.replace(/[\\/]+$/, "");
  const full = path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path) ? path : `${base}/${path}`;
  const relative = full.startsWith(`${base}/`) ? full.slice(base.length + 1) : full;
  return /\s/.test(relative) ? `"${relative}"` : relative;
}

/** What the view a command runs in must offer. */
export type Needs = "thread" | "work" | "none";

export interface SlashCommand {
  name: string;
  /** Mightling's own words (the vendor's are its copy). */
  description: string;
  needs: Needs;
  /** Shown after the name in the menu when the command takes words. */
  args?: string;
}

/**
 * The commands of the TUI the app runs (DESKTOP_PARITY §2.3 row "Slash commands"), each through a
 * method the policy allows or a window action, plus Mightling's `/airgapped` and `/cavemode`. `/review`, `/goal`,
 * `/plan`, `/memories`, `/agents` and the other launcher commands come with the items that add
 * their methods or views.
 */
export const SLASH_COMMANDS: SlashCommand[] = [
  { name: "new", description: "Start a new thread", needs: "none" },
  { name: "fork", description: "Continue this conversation in a copy, leaving this one as it is", needs: "thread" },
  { name: "compact", description: "Summarise the conversation so far to free context", needs: "thread" },
  { name: "edit", description: "Take back the last message and edit it", needs: "thread" },
  { name: "rename", description: "Give this thread a name", needs: "thread", args: "<name>" },
  { name: "archive", description: "Archive this thread", needs: "thread" },
  { name: "model", description: "Choose the model and how hard it thinks", needs: "none" },
  { name: "goal", description: "Set the thread's goal (the agent works until it is met), or show it", needs: "thread", args: "[objective]" },
  { name: "plan", description: "Plan mode: the agent plans with you before it changes anything", needs: "none", args: "[what to plan]" },
  { name: "permissions", description: "Choose what the agent may do without asking", needs: "work" },
  { name: "status", description: "Show the model, permissions, folder and context use", needs: "none" },
  { name: "skills", description: "The skills this folder can use ($ inserts one)", needs: "none" },
  { name: "mention", description: "Insert a file from this folder (@ does the same)", needs: "work" },
  { name: "mcp", description: "The MCP servers and their tools; add one", needs: "none" },
  { name: "apps", description: "The apps (Gmail, Drive …) and whether each is on", needs: "none" },
  { name: "diff", description: "Show how this folder differs from its remote branch", needs: "work" },
  { name: "copy", description: "Copy the last answer", needs: "thread" },
  { name: "resume", description: "Find an earlier thread", needs: "none" },
  { name: "memories", description: "Turn memories off or on for this thread", needs: "thread", args: "[on|off]" },
  { name: "agents", description: "Show this thread's subagents, to open or stop them", needs: "thread" },
  { name: "night", description: "Night Shift: queue a task for tonight, or open Scheduled", needs: "none", args: "[add <task>]" },
  { name: "review", description: "Have the agent review the uncommitted changes, a branch, a commit, or what you ask", needs: "thread", args: "[base <branch> | commit <sha> | instructions]" },
  { name: "airgapped", description: "Show the air-gap level, or set the default for new threads", needs: "none", args: "[default off|on]" },
  { name: "cavemode", description: "How terse answers are: show the level, set it for this thread, or the default", needs: "none", args: "[off|lite|full|ultra | default <level|built-in>]" },
];

export interface CommandContext {
  view: "ask" | "work";
  hasThread: boolean;
}

export function commandAvailable(command: SlashCommand, context: CommandContext): boolean {
  if (command.needs === "thread") return context.hasThread;
  if (command.needs === "work") return context.view === "work";
  return true;
}

/** The commands whose names start with `query`, then those that contain it, for the `/` menu. */
export function matchCommands(query: string, context: CommandContext): SlashCommand[] {
  const q = query.toLowerCase();
  const usable = SLASH_COMMANDS.filter((command) => commandAvailable(command, context));
  return [...usable.filter((c) => c.name.startsWith(q)), ...usable.filter((c) => !c.name.startsWith(q) && c.name.includes(q))];
}

/** A message that is a slash command, split into the command and its words; null for ordinary text. */
export function parseSlash(text: string): { command: SlashCommand; args: string } | null {
  const match = text.trim().match(/^\/([a-z-]+)(?:\s+([\s\S]*))?$/);
  if (!match) return null;
  const command = SLASH_COMMANDS.find((c) => c.name === match[1]);
  return command ? { command, args: (match[2] ?? "").trim() } : null;
}

/** Skills whose name or description matches, enabled ones first. */
export function matchSkills(skills: SkillMetadata[], query: string): SkillMetadata[] {
  const q = query.toLowerCase();
  return skills
    .filter((skill) => skill.name.toLowerCase().includes(q) || (!!q && skill.description.toLowerCase().includes(q)))
    .sort((a, b) => Number(b.enabled) - Number(a.enabled) || Number(!b.name.toLowerCase().startsWith(q)) - Number(!a.name.toLowerCase().startsWith(q)) || a.name.localeCompare(b.name));
}

/**
 * A turn's input from the composer: the text, a `skill` item for every `$name` that names a known
 * skill (as the TUI sends them, so the server loads its instructions), and each image by its path.
 */
export function composeInput(text: string, skills: SkillMetadata[], images: string[] = []): UserInput[] {
  const inputs: UserInput[] = text.trim() ? [{ type: "text", text, text_elements: [] }] : [];
  const named = new Set<string>();
  for (const match of text.matchAll(/(?:^|\s)\$([A-Za-z0-9_.:-]+)/g)) named.add(match[1]);
  for (const name of named) {
    const skill = skills.find((s) => s.name === name && s.enabled);
    if (skill) inputs.push({ type: "skill", name: skill.name, path: skill.path });
  }
  for (const path of images) inputs.push({ type: "localImage", path });
  return inputs;
}

/**
 * The prompts ↑ walks back through when the composer is empty, newest last: this window's own
 * sends first, then the shown thread's earlier messages it has not seen sent.
 */
export class PromptHistory {
  private readonly sent: string[] = [];
  private cursor: number | null = null;
  private stash = "";

  push(text: string): void {
    const value = text.trim();
    if (!value) return;
    if (this.sent[this.sent.length - 1] !== value) this.sent.push(value);
    if (this.sent.length > 100) this.sent.shift();
    this.cursor = null;
  }

  /** One step older; `draft` is what the composer holds now (kept for the walk back down). */
  older(draft: string, thread: string[] = []): string | null {
    const list = this.entries(thread);
    if (list.length === 0) return null;
    if (this.cursor === null) {
      this.stash = draft;
      this.cursor = list.length;
    }
    if (this.cursor === 0) return null;
    this.cursor--;
    return list[this.cursor];
  }

  /** One step newer, ending at the draft the walk started from. */
  newer(thread: string[] = []): string | null {
    if (this.cursor === null) return null;
    const list = this.entries(thread);
    this.cursor++;
    if (this.cursor >= list.length) {
      this.cursor = null;
      return this.stash;
    }
    return list[this.cursor];
  }

  /** The walk is over (the user typed). */
  reset(): void {
    this.cursor = null;
  }

  get walking(): boolean {
    return this.cursor !== null;
  }

  private entries(thread: string[]): string[] {
    const seen = new Set(this.sent);
    return [...thread.filter((text) => !seen.has(text)), ...this.sent];
  }
}

/** The text of every user message in a thread, oldest first. */
export function userTexts(view: ThreadView | null): string[] {
  if (!view) return [];
  const texts: string[] = [];
  for (const turn of view.turns) {
    for (const item of turn.items) {
      if (item.type !== "userMessage") continue;
      const text = item.content.map((input) => (input.type === "text" ? input.text : "")).join("").trim();
      if (text) texts.push(text);
    }
  }
  return texts;
}

/** The last turn that carries a user message, which Esc Esc takes back, and that message's text. */
export function lastUserTurn(view: ThreadView | null): { turnId: string; text: string } | null {
  if (!view) return null;
  for (let index = view.turns.length - 1; index >= 0; index--) {
    const turn = view.turns[index];
    const message = turn.items.find((item) => item.type === "userMessage");
    if (message && message.type === "userMessage") {
      return { turnId: turn.id, text: message.content.map((input) => (input.type === "text" ? input.text : "")).join("") };
    }
  }
  return null;
}

/** What Enter does in the composer under the window's preference: send, or a new line. */
export function enterSends(event: { key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; altKey: boolean }, behaviour: "enter" | "ctrlEnter"): boolean {
  if (event.key !== "Enter" || event.altKey) return false;
  if (behaviour === "ctrlEnter") return event.ctrlKey || event.metaKey;
  return !event.shiftKey;
}

/** The reasoning efforts the model's catalog entry allows, and the one it uses by default. */
export function effortChoices(model: Model | null): { efforts: string[]; preferred: string | null } {
  if (!model) return { efforts: [], preferred: null };
  return { efforts: model.supportedReasoningEfforts.map((option) => option.reasoningEffort), preferred: model.defaultReasoningEffort };
}

/** The effort sent with a turn: the chosen one if this model allows it, else none (the model's default). */
export function effortFor(model: Model | null, chosen: string | null): string | null {
  if (!chosen || !model) return null;
  return model.supportedReasoningEfforts.some((option) => option.reasoningEffort === chosen) ? chosen : null;
}

/** `recent` with `model` moved to the front, at most five. */
export function rememberModel(recent: string[], model: string): string[] {
  return [model, ...recent.filter((entry) => entry !== model)].slice(0, 5);
}

/**
 * The answer Enter (approve) or Esc (decline) gives an open approval, by its kind; null for a
 * request the keys do not answer (a question needs its fields filled).
 */
export function keyAnswer(method: ServerRequest["method"], key: "approve" | "decline", params: Record<string, unknown> = {}): object | null {
  switch (method) {
    case "item/commandExecution/requestApproval":
    case "item/fileChange/requestApproval":
      return { decision: key === "approve" ? "accept" : "decline" };
    case "execCommandApproval":
    case "applyPatchApproval":
      return { decision: key === "approve" ? "approved" : { denied: { rejection: "declined in the app" } } };
    case "item/permissions/requestApproval": {
      if (key === "decline") return { permissions: {}, scope: "turn" };
      const requested = (params.permissions ?? {}) as Record<string, unknown>;
      return { permissions: Object.fromEntries(Object.entries(requested).filter(([, value]) => value !== null && value !== undefined)), scope: "turn" };
    }
    default:
      return null;
  }
}

/** The collaboration mode a turn carries (`turn/start` `collaborationMode`): Plan mode, or none for the default. */
export function collaborationMode(mode: "default" | "plan", model: string | null, effort: string | null): { mode: "plan" | "default"; settings: { model: string; reasoning_effort: string | null; developer_instructions: null } } | null {
  if (mode !== "plan" || !model) return null;
  return { mode: "plan", settings: { model, reasoning_effort: effort, developer_instructions: null } };
}

/** The target `/review <args>` names: uncommitted changes, `base <branch>`, `commit <sha>`, or instructions of its own. */
export function reviewTarget(args: string): { type: "uncommittedChanges" } | { type: "baseBranch"; branch: string } | { type: "commit"; sha: string; title: string | null } | { type: "custom"; instructions: string } {
  const words = args.trim();
  if (!words) return { type: "uncommittedChanges" };
  const base = words.match(/^base\s+(\S+)$/);
  if (base) return { type: "baseBranch", branch: base[1] };
  const commit = words.match(/^commit\s+([0-9a-fA-F]{4,64})$/);
  if (commit) return { type: "commit", sha: commit[1], title: null };
  return { type: "custom", instructions: words };
}
