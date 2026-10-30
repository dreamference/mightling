// Tool calls in full (DESKTOP_PARITY §4.5 item 26, DESKTOP §16 B11): an MCP tool call with its
// arguments, its progress and its result folded under it; the agent's other tools (dynamic tools,
// image views, hook prompts, waits, generated images, function outputs, web searches) with a label in
// words and what they carried, instead of their type's name. Pure: no DOM.

import type { ThreadItem } from "./protocol/v2/ThreadItem";

import { toolLabel } from "./windowtools";

/** One folded part of a tool item: a title and its text (pretty JSON or plain words). */
export interface ToolPart {
  title: string;
  text: string;
}

export interface ToolView {
  label: string;
  /** A short state after the label: "running", "failed", how long it took. */
  state: string | null;
  parts: ToolPart[];
  failed: boolean;
}

/** The most of one part shown; the rest is cut with a note. */
export const PART_CAP = 20_000;

function capped(text: string): string {
  return text.length > PART_CAP ? `${text.slice(0, PART_CAP)}\n… (${(text.length - PART_CAP).toLocaleString()} more characters)` : text;
}

/** A JSON value as text: a string as itself, anything else pretty-printed; null for nothing. */
export function jsonText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value.trim() ? value : null;
  if (typeof value === "object" && Object.keys(value as object).length === 0) return null;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** How long something took, in words: 850 ms, 2.4 s, 3 min. */
export function took(ms: number | null | undefined): string | null {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return null;
  // Each unit is chosen on the rounded value, so 999.6 ms is "1.0 s" and not "1000 ms".
  if (Math.round(ms) < 1000) return `${Math.round(ms)} ms`;
  if (Math.round(ms / 100) < 100) return `${(ms / 1000).toFixed(1)} s`;
  if (Math.round(ms / 1000) < 60) return `${Math.round(ms / 1000)} s`;
  return `${Math.round(ms / 60_000)} min`;
}

/** An MCP result's content blocks as text: text as itself, images and resources named. */
export function mcpContent(content: unknown[]): string {
  return content.map((block) => {
    const entry = (block ?? {}) as Record<string, unknown>;
    if (entry.type === "text" && typeof entry.text === "string") return entry.text;
    if (entry.type === "image") return `[an image${typeof entry.mimeType === "string" ? `, ${entry.mimeType}` : ""}]`;
    if (entry.type === "audio") return "[audio]";
    if (entry.type === "resource_link" || entry.type === "resource") {
      const resource = (entry.resource ?? entry) as Record<string, unknown>;
      if (typeof resource.text === "string") return resource.text;
      return `[resource ${typeof resource.uri === "string" ? resource.uri : ""}]`.replace(" ]", "]");
    }
    return jsonText(block) ?? "";
  }).filter(Boolean).join("\n\n");
}

const part = (title: string, text: string | null): ToolPart[] => (text && text.trim() ? [{ title, text: capped(text) }] : []);

/** What a tool item shows; null for an item that is not a tool's. */
export function toolView(item: ThreadItem, progress: string[] = []): ToolView | null {
  switch (item.type) {
    case "mcpToolCall": {
      const failed = item.status === "failed" || Boolean(item.error);
      const result = item.result ? [mcpContent(item.result.content ?? []), jsonText(item.result.structuredContent)].filter(Boolean).join("\n\n") : null;
      return {
        label: `${item.server} · ${item.tool}`,
        state: item.status === "inProgress" ? (progress.length ? progress[progress.length - 1] : "running") : failed ? "failed" : took(item.durationMs),
        failed,
        parts: [...part("Arguments", jsonText(item.arguments)), ...part("Progress", progress.join("\n") || null), ...part("Error", item.error?.message ?? null), ...part("Result", result)],
      };
    }
    case "dynamicToolCall": {
      const text = (item.contentItems ?? []).map((content) => (content.type === "inputText" ? content.text : content.type === "inputImage" ? "[an image]" : "[audio]")).join("\n").trim();
      const own = toolLabel(item.tool);
      return {
        label: own ?? `${item.namespace ? `${item.namespace} · ` : ""}${item.tool}`,
        state: item.status === "inProgress" ? "running" : item.success === false ? (own ? "not available" : "failed") : null,
        failed: item.success === false && !own,
        // The window's own tools carry nothing worth showing as arguments (a line count).
        parts: [...(own ? [] : part("Arguments", jsonText(item.arguments))), ...part(own ? "What it read" : "Result", text || null)],
      };
    }
    case "imageView":
      return { label: "Looked at an image", state: null, failed: false, parts: part("File", item.path) };
    case "hookPrompt":
      return { label: "A hook added to the conversation", state: null, failed: false, parts: part("What it added", item.fragments.map((fragment) => fragment.text).join("\n\n")) };
    case "sleep":
      return { label: "Waited", state: took(item.durationMs), failed: false, parts: [] };
    case "imageGeneration": {
      const failed = Boolean(item.failure);
      return { label: "Made an image", state: failed ? "failed" : item.status === "completed" ? null : item.status, failed, parts: [...part("Prompt", item.revisedPrompt), ...part("Saved as", item.savedPath ?? null)] };
    }
    case "functionCallOutput": {
      const output = typeof item.output === "string" ? item.output : item.output.map((content) => (content.type === "input_text" ? content.text : `[${content.type.replace("input_", "")}]`)).join("\n");
      return { label: `${item.namespace ? `${item.namespace} · ` : ""}${item.name}`, state: null, failed: false, parts: part("Output", output) };
    }
    case "webSearch": {
      const action = item.action;
      const what = action?.type === "openPage" ? `Opened ${action.url ?? "a page"}` : action?.type === "findInPage" ? `Looked for “${action.pattern ?? ""}” in ${action.url ?? "a page"}` : null;
      const queries = action?.type === "search" ? (action.queries?.length ? action.queries : [action.query ?? item.query]) : [item.query];
      return { label: what ?? `Searched the web: ${queries.filter(Boolean).join("; ") || "…"}`, state: null, failed: false, parts: part("Results", item.results?.length ? jsonText(item.results) : null) };
    }
    default:
      return null;
  }
}
