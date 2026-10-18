// Files read through the server (DESKTOP_PARITY §2.8): the file tree's entries, a file tab's text
// and its highlighting. `fs/readFile` answers base64; a file with a NUL byte in its first 8 KB, or
// that is not UTF-8, is shown as binary rather than as mojibake. Highlighting is highlight.js's core
// with the languages the agent edits most, registered here once (BSD-3-Clause, bundled).

import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import kotlin from "highlight.js/lib/languages/kotlin";
import makefile from "highlight.js/lib/languages/makefile";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

import type { FsReadDirectoryEntry } from "./protocol/v2/FsReadDirectoryEntry";
import { baseName } from "./tabs";

export { baseName };

const LANGUAGES = { bash, c, cpp, css, diff, dockerfile, go, ini, java, javascript, json, kotlin, makefile, markdown, python, rust, sql, typescript, xml, yaml };
for (const [name, language] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, language);

/** File extensions (and a few whole names) to the language that highlights them. */
const BY_EXTENSION: Record<string, keyof typeof LANGUAGES> = {
  sh: "bash", bash: "bash", zsh: "bash", fish: "bash",
  c: "c", h: "c", cc: "cpp", cpp: "cpp", cxx: "cpp", hpp: "cpp", hh: "cpp", cu: "cpp", cuh: "cpp",
  css: "css", diff: "diff", patch: "diff", go: "go", ini: "ini", cfg: "ini", toml: "ini", conf: "ini",
  java: "java", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  json: "json", jsonl: "json", kt: "kotlin", kts: "kotlin", md: "markdown", markdown: "markdown",
  py: "python", pyi: "python", rs: "rust", sql: "sql", ts: "typescript", tsx: "typescript", mts: "typescript",
  html: "xml", htm: "xml", xml: "xml", svg: "xml", vue: "xml", yml: "yaml", yaml: "yaml",
};
const BY_NAME: Record<string, keyof typeof LANGUAGES> = { dockerfile: "dockerfile", makefile: "makefile", gnumakefile: "makefile" };

/** The language of a file by its name; null when none is bundled for it. */
export function languageOf(path: string): string | null {
  const name = baseName(path).toLowerCase();
  if (BY_NAME[name]) return BY_NAME[name];
  const dot = name.lastIndexOf(".");
  return dot > 0 ? BY_EXTENSION[name.slice(dot + 1)] ?? null : null;
}

/** The folder a path is in ("/" for a top-level one). */
export function parentOf(path: string): string {
  const cut = path.replace(/[\\/]+$/, "").lastIndexOf("/");
  return cut <= 0 ? "/" : path.slice(0, cut);
}

/** `child` under the folder `dir`, absolute as the server wants it. */
export function joinPath(dir: string, child: string): string {
  return `${dir.replace(/[\\/]+$/, "")}/${child}`;
}

/** `path` relative to `root` when it is inside it, else the path as it is. */
export function relativeTo(root: string | null, path: string): string {
  if (!root) return path;
  const base = root.replace(/[\\/]+$/, "");
  return path.startsWith(`${base}/`) ? path.slice(base.length + 1) : path;
}

/** Names the tree leaves out: the repository's own database, which nobody reads by hand. */
const HIDDEN = new Set([".git"]);

/** A folder's entries as the tree shows them: folders first, then files, each by name ignoring case. */
export function treeEntries(entries: FsReadDirectoryEntry[]): FsReadDirectoryEntry[] {
  return entries.filter((entry) => !HIDDEN.has(entry.fileName))
    .sort((a, b) => Number(b.isDirectory) - Number(a.isDirectory) || a.fileName.localeCompare(b.fileName, undefined, { sensitivity: "base", numeric: true }));
}

export interface FileText {
  /** The text, or null for a binary file. */
  text: string | null;
  bytes: number;
}

/** Decodes what `fs/readFile` answered. */
export function decodeFile(dataBase64: string): FileText {
  const raw = atob(dataBase64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index++) bytes[index] = raw.charCodeAt(index);
  if (bytes.subarray(0, 8192).includes(0)) return { text: null, bytes: bytes.length };
  try {
    return { text: new TextDecoder("utf-8", { fatal: true }).decode(bytes), bytes: bytes.length };
  } catch {
    return { text: null, bytes: bytes.length };
  }
}

/** The raw bytes of what `fs/readFile` answered (images, PDFs). */
export function bytesOf(dataBase64: string): Uint8Array<ArrayBuffer> {
  const raw = atob(dataBase64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index++) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

/** Files bigger than this open as a note with an editor button, not in a tab (the read is the server's, whole). */
export const LARGEST_SHOWN = 4 * 1024 * 1024;

/** Files with more lines than this are shown without highlighting, which would stall the page. */
export const LARGEST_HIGHLIGHTED = 20_000;

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** The text as HTML, highlighted when its language is bundled; always escaped. */
export function highlighted(text: string, language: string | null): string {
  if (!language || text.split("\n").length > LARGEST_HIGHLIGHTED) return escapeHtml(text);
  try {
    return hljs.highlight(text, { language, ignoreIllegals: true }).value;
  } catch {
    return escapeHtml(text);
  }
}

/** How many lines the text shows (a final newline ends the last line rather than starting one). */
export function lineCount(text: string): number {
  if (text === "") return 1;
  const lines = text.split("\n").length;
  return text.endsWith("\n") ? lines - 1 : lines;
}

/** Human size: 812 B, 14.2 KB, 3.1 MB. */
export function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** What Ctrl+P was given: a name to search for, and a line after a colon (`hero.ts:12`, or `:12` for the file in front). */
export function parseFileQuery(query: string): { text: string; line: number | null } {
  const match = /^(.*?):(\d+)(?::\d+)?\s*$/.exec(query.trim());
  if (!match) return { text: query.trim(), line: null };
  const line = Number(match[2]);
  return { text: match[1].trim(), line: line > 0 ? line : null };
}

/** A line the user typed for go to line, kept inside the file; null when it is not a number. */
export function lineWithin(typed: string, lines: number): number | null {
  const number = Number.parseInt(typed.trim().replace(/^:/, ""), 10);
  if (!Number.isFinite(number)) return null;
  return Math.min(Math.max(number, 1), Math.max(lines, 1));
}
