// The Activity view (DESKTOP_PARITY §2.4, Phase 3 item 5; Ctrl+Alt+U): what the agent did across
// threads, newest first — commands it ran, files it changed, tools and searches it used, subagents
// it started — read from the threads' turns (`thread/turns/list` with full items). Nothing is
// recorded for it: it is a view over the session files the server already keeps.

import type { Thread } from "./protocol/v2/Thread";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import type { Turn } from "./protocol/v2/Turn";

export type ActivityKind = "command" | "files" | "tool" | "web" | "agents";

export interface ActivityEntry {
  /** Unique within the view: thread, turn and item. */
  key: string;
  threadId: string;
  thread: string;
  cwd: string;
  /** Unix seconds: when the item's turn started (items carry no time of their own). */
  at: number;
  kind: ActivityKind;
  summary: string;
  /** A command that exited non-zero, a change or a tool call that failed. */
  failed: boolean;
}

export const KIND_LABELS: Record<ActivityKind, string> = {
  command: "Commands",
  files: "Files",
  tool: "Tools",
  web: "Web",
  agents: "Subagents",
};

function oneLine(text: string, max = 140): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** What one item amounts to in the timeline; null for what is talk rather than action. */
export function describeItem(item: ThreadItem): { kind: ActivityKind; summary: string; failed: boolean } | null {
  switch (item.type) {
    case "commandExecution":
      return { kind: "command", summary: `Ran ${oneLine(item.command)}${item.exitCode !== null && item.exitCode !== 0 ? ` (exit ${item.exitCode})` : ""}`, failed: item.exitCode !== null && item.exitCode !== 0 };
    case "fileChange": {
      const names = item.changes.map((change) => change.path.split(/[\\/]/).pop() ?? change.path);
      const verb = item.changes.every((change) => change.kind.type === "add") ? "Added" : item.changes.every((change) => change.kind.type === "delete") ? "Deleted" : "Edited";
      const shown = names.slice(0, 3).join(", ") + (names.length > 3 ? ` and ${names.length - 3} more` : "");
      return { kind: "files", summary: `${verb} ${shown}`, failed: item.status === "failed" || item.status === "declined" };
    }
    case "mcpToolCall":
      return { kind: "tool", summary: `Called ${item.server}.${item.tool}${item.error ? `: ${oneLine(item.error.message, 80)}` : ""}`, failed: Boolean(item.error) };
    case "dynamicToolCall":
      return { kind: "tool", summary: `Called ${item.namespace ? `${item.namespace}.` : ""}${item.tool}`, failed: item.success === false };
    case "webSearch":
      return { kind: "web", summary: `Searched the web${item.query ? `: ${oneLine(item.query, 80)}` : ""}`, failed: false };
    case "collabAgentToolCall":
      return { kind: "agents", summary: `Subagents: ${item.tool}${item.prompt ? ` — ${oneLine(item.prompt, 100)}` : ""}`, failed: false };
    default:
      return null;
  }
}

/** The timeline of one thread's turns. */
export function activityOf(thread: Thread, turns: Turn[]): ActivityEntry[] {
  const entries: ActivityEntry[] = [];
  const name = thread.name || thread.preview || "(new thread)";
  for (const turn of turns) {
    const at = turn.startedAt ?? turn.completedAt ?? thread.updatedAt;
    for (const item of turn.items) {
      const described = describeItem(item);
      if (!described) continue;
      entries.push({ key: `${thread.id}/${turn.id}/${item.id}`, threadId: thread.id, thread: name, cwd: thread.cwd, at, ...described });
    }
  }
  return entries;
}

/** Every thread's timeline merged, newest first (within a turn, the item order kept), with the filters applied. */
export function merged(lists: ActivityEntry[][], kinds: ReadonlySet<ActivityKind>, project: string | null): ActivityEntry[] {
  const all = lists.flat().filter((entry) => kinds.has(entry.kind) && (!project || entry.cwd === project || entry.cwd.startsWith(`${project}/`)));
  const order = new Map(all.map((entry, index) => [entry.key, index]));
  return all.sort((a, b) => b.at - a.at || (order.get(b.key) ?? 0) - (order.get(a.key) ?? 0));
}

/** "Today", "Yesterday", or the date, for the timeline's day headings. */
export function dayLabel(at: number, now = Date.now()): string {
  const day = new Date(at * 1000);
  const today = new Date(now);
  const start = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((start(today) - start(day)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return day.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}
