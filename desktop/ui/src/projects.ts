// Projects (DESKTOP_PARITY §4.5 item 13, decided by the owner on 2026-10-21): opt-in server objects
// (`project/create|list|read|update`). A project has a name, its folders (`roots`; the first is the
// primary: new threads start there, git and AGENTS.md discovery use it), standing instructions and
// knowledge files, for Ask and for Work. A thread joins with `thread/start`'s or
// `thread/metadata/update`'s `projectId`; a thread with none keeps the folder grouping it had.
// Mightling's own fields live in the project's metadata, a map the server replaces whole on update.
// Pure: no DOM, no client.

import type { Project } from "./protocol/v2/Project";

/** The project's standing instructions, given to every thread of the project. */
export const INSTRUCTIONS_KEY = "mightling.instructions";
/** "1" when the project was removed from the sidebar; its threads and settings stay, Restore brings it back. */
export const REMOVED_KEY = "mightling.removed";

/** A knowledge file of a project, as the desktop app's main process lists them. */
export interface KnowledgeFile {
  name: string;
  path: string;
  size: number;
}

export function primaryFolder(project: Pick<Project, "roots">): string | null {
  return project.roots[0]?.path ?? null;
}

/** The folders after the primary one: a thread of the project may read and change them too (`runtimeWorkspaceRoots`). */
export function secondaryFolders(project: Pick<Project, "roots">): string[] {
  return project.roots.slice(1).map((root) => root.path);
}

export function instructionsOf(project: Pick<Project, "metadata">): string {
  return project.metadata[INSTRUCTIONS_KEY] ?? "";
}

export function isRemoved(project: Pick<Project, "metadata">): boolean {
  return project.metadata[REMOVED_KEY] === "1";
}

/** The projects the sidebar shows, in the server's order (its `position`). */
export function shownProjects<T extends Pick<Project, "metadata" | "position">>(projects: T[]): T[] {
  return projects.filter((project) => !isRemoved(project)).sort((a, b) => a.position - b.position);
}

/**
 * Items in the user's order (dragged in the sidebar, kept in the window's preferences by key) and,
 * after those, the rest in the order they came: a stable order that activity never changes.
 */
export function ordered<T>(items: T[], key: (item: T) => string, saved: string[]): T[] {
  const rank = new Map(saved.map((entry, index) => [entry, index]));
  const known = items.filter((item) => rank.has(key(item))).sort((a, b) => (rank.get(key(a)) ?? 0) - (rank.get(key(b)) ?? 0));
  return [...known, ...items.filter((item) => !rank.has(key(item)))];
}

/** The saved order after dragging `moved` onto `target` (placed just before it), over the keys shown now. */
export function movedBefore(shown: string[], saved: string[], moved: string, target: string): string[] {
  const order = [...saved.filter((entry) => shown.includes(entry)), ...shown.filter((entry) => !saved.includes(entry))].filter((entry) => entry !== moved);
  const at = order.indexOf(target);
  order.splice(at < 0 ? order.length : at, 0, moved);
  return order;
}

/** The roots with `path` made the primary one (the vendor's Make primary). */
export function withPrimary(roots: Project["roots"], path: string): Project["roots"] {
  const chosen = roots.find((root) => root.path === path);
  return chosen ? [chosen, ...roots.filter((root) => root !== chosen)] : roots;
}

/** The metadata after one change: every other key kept, an empty value removing its key. */
export function withMetadata(metadata: Project["metadata"], changes: Record<string, string | null>): Record<string, string> {
  const next: Record<string, string> = {};
  for (const [key, value] of Object.entries(metadata)) if (typeof value === "string") next[key] = value;
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === "") delete next[key];
    else next[key] = value;
  }
  return next;
}

/** A name for a new project from its first folder: the folder's own name. */
export function nameFromFolder(folder: string): string {
  return folder.split(/[\\/]/).filter(Boolean).pop() ?? folder;
}

/** What a project's thread is told before its first message, and again after the project changed: its name, instructions and knowledge files. */
export function projectContext(project: Pick<Project, "name" | "roots" | "metadata">, files: KnowledgeFile[]): string | null {
  const instructions = instructionsOf(project).trim();
  const secondary = secondaryFolders(project);
  if (!instructions && files.length === 0 && secondary.length === 0) return null;
  const parts = [`This conversation belongs to the project "${project.name}".`];
  if (instructions) parts.push(`The project's standing instructions, which apply to every conversation in it:\n${instructions}`);
  if (secondary.length) parts.push(`Besides the working folder, the project's other folders, which you may read and change: ${secondary.join(", ")}`);
  if (files.length) parts.push(`The project's knowledge files; read the ones that bear on the request before answering:\n${files.map((file) => `- ${file.path}`).join("\n")}`);
  return parts.join("\n\n");
}

/** A short fingerprint of a context, so a thread is told again only after it changed. */
export function contextStamp(context: string | null): string {
  if (!context) return "";
  let hash = 5381;
  for (let index = 0; index < context.length; index++) hash = ((hash * 33) ^ context.charCodeAt(index)) >>> 0;
  return `${context.length}:${hash.toString(16)}`;
}

const OPEN = "<project_context>";

/**
 * The text item that carries a project's context into a turn, after the user's own input: the server
 * titles a thread by its first message's text, which then starts with what the user wrote.
 */
export function contextItem(context: string): { type: "text"; text: string; text_elements: [] } {
  return { type: "text", text: `${OPEN}\n${context}\n</project_context>`, text_elements: [] };
}

/** Whether an input item's text is a project context the window added (never shown as the user's words). */
export function isContextText(text: string): boolean {
  return text.startsWith(OPEN);
}

/** A text (a thread's preview, a message) without any project context the window added. */
export function withoutContext(text: string): string {
  const at = text.indexOf(OPEN);
  return at < 0 ? text : text.slice(0, at).trimEnd();
}
