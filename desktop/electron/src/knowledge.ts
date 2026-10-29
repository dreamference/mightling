// A project's knowledge files (DESKTOP_PARITY §4.5 item 13): copies the user chose, kept in
// `$CODEX_HOME/projects/<projectId>/knowledge/`, so the project's threads can read them whatever folder
// they work in (the sandbox reads everywhere) and the originals are never touched. The page names a
// project by its server id and a file by its plain name only; nothing outside the folder is reached.

import fs from "node:fs/promises";
import path from "node:path";

import * as userFs from "./userfs";

/** A project id as the server makes them (letters, digits, `-` and `_`); anything else is refused. */
const PROJECT_ID = /^[A-Za-z0-9_-]{1,128}$/;

/** At most this many bytes in one file, so a project's knowledge stays something a 27B model can read. */
export const KNOWLEDGE_FILE_LIMIT = 25 * 1024 * 1024;

export interface KnowledgeFile {
  name: string;
  path: string;
  size: number;
}

/**
 * The `ling-docs` collection a project's knowledge is searched in (DESKTOP_PARITY_CLAUDE §4.7):
 * `project-` and the id, as a collection name may be (lower case, 40 at most). The page's
 * `knowledgeCollection` (desktop/ui/src/projects.ts) names it the same way.
 */
export function knowledgeCollection(projectId: string): string {
  if (!PROJECT_ID.test(projectId)) throw new Error(`not a project id: ${projectId}`);
  return `project-${projectId.toLowerCase().replace(/[^a-z0-9_-]/g, "-")}`.slice(0, 40);
}

export function knowledgeFolder(codexHome: string, projectId: string): string {
  if (!PROJECT_ID.test(projectId)) throw new Error(`not a project id: ${projectId}`);
  return path.join(codexHome, "projects", projectId, "knowledge");
}

/** A file name with no folder in it, not hidden, not `.` or `..`. */
function plainName(name: string): string {
  const base = path.basename(name);
  if (!base || base !== name || base.startsWith(".")) throw new Error(`not a file name: ${name}`);
  return base;
}

/** The project's knowledge files, by name; none when it has no folder yet. */
export async function listKnowledge(codexHome: string, projectId: string): Promise<KnowledgeFile[]> {
  const folder = knowledgeFolder(codexHome, projectId);
  if (!(await userFs.isDirectory(folder))) return [];
  const files: KnowledgeFile[] = [];
  for (const entry of await userFs.readdir(folder)) {
    if (!entry.isFile() || entry.name.startsWith(".")) continue;
    const place = path.join(folder, entry.name);
    const stats = await userFs.lstatOrNull(place);
    if (stats?.isFile()) files.push({ name: entry.name, path: place, size: stats.size });
  }
  return files.sort((a, b) => a.name.localeCompare(b.name));
}

/** Copies files into the project's knowledge, a name already there getting ` (2)`, ` (3)` …; a folder, a link or a file over the limit is refused. */
export async function addKnowledge(codexHome: string, projectId: string, sources: string[]): Promise<KnowledgeFile[]> {
  const folder = knowledgeFolder(codexHome, projectId);
  await userFs.mkdir(folder);
  const taken = new Set((await listKnowledge(codexHome, projectId)).map((file) => file.name));
  for (const source of sources) {
    const stats = await userFs.lstatOrNull(source);
    if (!stats?.isFile()) throw new Error(`${source} is not a file`);
    if (stats.size > KNOWLEDGE_FILE_LIMIT) throw new Error(`${path.basename(source)} is larger than ${KNOWLEDGE_FILE_LIMIT / (1024 * 1024)} MB`);
    const extension = path.extname(source);
    const stem = path.basename(source, extension).replace(/^\.+/, "") || "file";
    // a name can be taken after the folder was listed (another add at once): the copy never replaces, so number on
    for (let count = 1; ; count++) {
      const name = count === 1 ? `${stem}${extension}` : `${stem} (${count})${extension}`;
      if (taken.has(name)) continue;
      taken.add(name);
      try {
        await userFs.copyFile(source, path.join(folder, name));
        break;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      }
    }
  }
  return listKnowledge(codexHome, projectId);
}

export async function removeKnowledge(codexHome: string, projectId: string, name: string): Promise<KnowledgeFile[]> {
  const place = path.join(knowledgeFolder(codexHome, projectId), plainName(name));
  await fs.rm(place, { force: true });
  return listKnowledge(codexHome, projectId);
}
