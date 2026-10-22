// Export a thread to a file (the owner's decision 6 of DESKTOP_PARITY §9.5, 2026-10-21; the local
// answer to the vendor's shared snapshot): Markdown or HTML, written on this machine and nothing
// uploaded. By default it holds the user's and the agent's messages and the files the agent changed,
// with paths, e-mail addresses and tokens redacted; commands and their output only when asked. The
// user sees the exact text before saving. Pure.

import { renderMarkdown } from "./markdown";
import { isContextText } from "./projects";
import type { ThreadItem } from "./protocol/v2/ThreadItem";
import { maskSecrets } from "./windowtools";

export interface ExportOptions {
  format: "markdown" | "html";
  redactPaths: boolean;
  redactEmails: boolean;
  redactTokens: boolean;
  includeCommands: boolean;
  includeReasoning: boolean;
}

export const DEFAULT_EXPORT: ExportOptions = { format: "markdown", redactPaths: true, redactEmails: true, redactTokens: true, includeCommands: false, includeReasoning: false };

export interface ExportSource {
  title: string;
  cwd: string;
  home: string | null;
  turns: { items: ThreadItem[] }[];
}

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * Redaction: the thread's folder becomes `.`, the home folder `~`, any other absolute path its last
 * part after `…/`; e-mail addresses `[email]`; tokens and keys as the window's tools mask them.
 */
export function redact(text: string, options: ExportOptions, places: { cwd: string; home: string | null }): string {
  let out = text;
  if (options.redactPaths) {
    for (const [place, alias] of [[places.cwd, "."], [places.home, "~"]] as const) {
      if (place && place.length > 1) out = out.replace(new RegExp(`${escapeRegExp(place.replace(/[\\/]+$/, ""))}(?=[\\\\/]|\\b|$)`, "g"), alias);
    }
    out = out.replace(/(^|[\s("'`=:])((?:\/[\w.@+-]+){2,}|[A-Za-z]:\\(?:[\w.@+ -]+\\)+[\w.@+ -]*)/g, (_, lead: string, path: string) => `${lead}…/${path.split(/[\\/]/).filter(Boolean).pop()}`);
  }
  if (options.redactEmails) out = out.replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "[email]");
  if (options.redactTokens) out = maskSecrets(out);
  return out;
}

const fence = (text: string, language = "") => {
  const longest = Math.max(2, ...[...text.matchAll(/`+/g)].map((match) => match[0].length));
  const ticks = "`".repeat(longest + 1);
  return `${ticks}${language}\n${text}\n${ticks}`;
};

/** One item as Markdown, or null when the options leave it out. */
function itemMarkdown(item: ThreadItem, options: ExportOptions): string | null {
  switch (item.type) {
    case "userMessage": {
      const text = item.content.map((input) => (input.type === "text" && !isContextText(input.text) ? input.text : input.type === "localImage" ? `[image: ${input.path.split(/[\\/]/).pop()}]` : "")).filter(Boolean).join("\n");
      return text ? `### You\n\n${text}` : null;
    }
    case "agentMessage":
      return item.text ? `### Mightling\n\n${item.text}` : null;
    case "reasoning": {
      const text = [...item.summary].join("\n").trim();
      return options.includeReasoning && text ? `> **Thinking:** ${text.replace(/\n/g, "\n> ")}` : null;
    }
    case "fileChange": {
      const parts = item.changes.map((change) => `**${change.kind.type === "add" ? "Added" : change.kind.type === "delete" ? "Deleted" : "Changed"} \`${change.path}\`**\n\n${fence(change.diff, "diff")}`);
      return parts.length ? parts.join("\n\n") : null;
    }
    case "commandExecution":
      return options.includeCommands ? `**Ran** \`${item.command}\`${item.exitCode !== null && item.exitCode !== 0 ? ` (exit ${item.exitCode})` : ""}${item.aggregatedOutput ? `\n\n${fence(item.aggregatedOutput.slice(-8000))}` : ""}` : null;
    default:
      return null;
  }
}

/** The thread as Markdown, redacted as the options say. */
export function threadMarkdown(source: ExportSource, options: ExportOptions): string {
  const body = source.turns.flatMap((turn) => turn.items.map((item) => itemMarkdown(item, options)).filter((part): part is string => Boolean(part)));
  const text = [`# ${source.title}`, `_Exported from Mightling on ${new Date().toISOString().slice(0, 10)}._`, ...body].join("\n\n") + "\n";
  return redact(text, options, { cwd: source.cwd, home: source.home });
}

const escapeHtml = (text: string) => text.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char);

/** The thread as one self-contained HTML page (no script, no remote resource), from the same Markdown. */
export function threadHtml(source: ExportSource, options: ExportOptions): string {
  const markdown = threadMarkdown(source, options);
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>${escapeHtml(source.title)}</title>
<style>
body { font: 15px/1.6 system-ui, sans-serif; max-width: 760px; margin: 32px auto; padding: 0 16px; color: #1f2328; background: #fff; }
pre { background: #f6f8fa; padding: 12px; border-radius: 8px; overflow: auto; font-size: 13px; }
code { font-family: ui-monospace, monospace; }
h3 { margin-top: 28px; font-size: 15px; }
blockquote { color: #59636e; border-left: 3px solid #d1d9e0; margin: 0; padding-left: 12px; }
@media (prefers-color-scheme: dark) { body { color: #ececec; background: #212121; } pre { background: #2f2f2f; } blockquote { color: #b4b4b4; border-color: #444; } }
</style></head>
<body>
${renderMarkdown(markdown)}
</body></html>
`;
}

/** A file name for the export: the title's words, safe on every system. */
export function exportName(title: string, format: ExportOptions["format"]): string {
  const base = title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "thread";
  return `${base}.${format === "html" ? "html" : "md"}`;
}
