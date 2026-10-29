// Memories one by one (DESKTOP_PARITY_CLAUDE §2.7, DESKTOP §16 B17): the files the memory pipeline
// consolidates under `$CODEX_HOME/memories/` (Codex's README for `memories/`): `memory_summary.md`,
// the compact summary new threads start with, and `MEMORY.md`, the index the agent searches. Settings
// › Memories shows them and saves the user's edits; nothing else in that folder (the raw memories,
// the rollout summaries, the pipeline's git baseline) is read or written here. No Electron here.

import fs from "node:fs";
import path from "node:path";

/** The files shown, in order, with what each is for. */
export const MEMORY_FILES = [
  { name: "memory_summary.md", about: "What every new thread starts with" },
  { name: "MEMORY.md", about: "The index the agent searches when a task needs it" },
] as const;

export type MemoryFileName = (typeof MEMORY_FILES)[number]["name"];

/** The largest file shown or saved. */
export const MEMORY_FILE_CAP = 256 * 1024;

const known = (name: unknown): name is MemoryFileName => MEMORY_FILES.some((file) => file.name === name);

/** The memory files that exist, with their text (cut at the cap) and when they changed. */
export async function listMemoryFiles(codexHome: string): Promise<{ name: MemoryFileName; about: string; text: string; modified: number; cut: boolean }[]> {
  const root = path.join(codexHome, "memories");
  const found = [];
  for (const file of MEMORY_FILES) {
    const full = path.join(root, file.name);
    try {
      const stat = await fs.promises.lstat(full);
      if (!stat.isFile()) continue;
      const handle = await fs.promises.open(full, "r");
      try {
        const buffer = Buffer.alloc(Math.min(stat.size, MEMORY_FILE_CAP));
        const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
        found.push({ name: file.name, about: file.about, text: buffer.subarray(0, bytesRead).toString("utf8"), modified: stat.mtimeMs, cut: stat.size > MEMORY_FILE_CAP });
      } finally {
        await handle.close();
      }
    } catch {
      // Not made yet.
    }
  }
  return found;
}

/** Saves the user's edit of one memory file, replacing it whole (written beside it, then renamed). */
export async function writeMemoryFile(codexHome: string, name: unknown, text: unknown): Promise<null> {
  if (!known(name)) throw new Error("not a memory file");
  if (typeof text !== "string" || Buffer.byteLength(text) > MEMORY_FILE_CAP) throw new Error(`a memory file holds at most ${MEMORY_FILE_CAP >> 10} KB`);
  const root = path.join(codexHome, "memories");
  const full = path.join(root, name);
  const stat = await fs.promises.lstat(full).catch(() => null);
  if (!stat || !stat.isFile()) throw new Error("that memory file is not there any more");
  const temp = `${full}.mightling-${process.pid}.tmp`;
  await fs.promises.writeFile(temp, text, { mode: 0o600 });
  await fs.promises.rename(temp, full);
  return null;
}
