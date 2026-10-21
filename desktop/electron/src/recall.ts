// Earlier questions for the agent (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY_CLAUDE.md §4.4,
// decided 2026-10-21): off by default; Settings › Memories turns it on. Then each Ask question, with
// its answers, is kept as a Markdown file under `$CODEX_HOME/ask-history/` (the page writes them from
// the server's turns, `recall/write`), and a skill of the user's, `$CODEX_HOME/skills/earlier-questions`,
// tells the agent to search them with `rg` and read them: local, read only, inside the sandbox it
// already has. Incognito questions are never kept, archived ones are dropped (`recall/prune`), and
// turning it off deletes the files and the skill. No Electron here.

import fs from "node:fs";
import path from "node:path";

import { validThreadId } from "./ask";

/** The folder of the copies, under `$CODEX_HOME`. */
export const HISTORY_DIR = "ask-history";
/** The skill's folder, under `$CODEX_HOME/skills` (the user's own skills). */
export const SKILL_DIR = path.join("skills", "earlier-questions");
/** The largest copy kept of one question (its start is kept beyond that). */
export const MAX_BYTES = 512 * 1024;
const INDEX = "index.json";

const historyDir = (home: string) => path.join(home, HISTORY_DIR);
const skillDir = (home: string) => path.join(home, SKILL_DIR);

/** The skill's text: when to look, how, and that it only reads. */
export function skillText(dir: string): string {
  return [
    "---",
    "name: earlier-questions",
    "description: Search the user's earlier Ask questions and the answers Mightling gave, kept on this machine as Markdown files. Use when the user refers to something asked or answered before (\"what did you tell me about…\", \"like last time\", \"that article we found\"), or wants to build on an earlier question.",
    "---",
    "",
    "# Earlier questions",
    "",
    `The user's earlier questions in Ask, each with its answers, are Markdown files in \`${dir}\`: one file per question, named by its thread id. A file's first lines give the question's title, when it was asked and the folder it worked in (files made for that question are there).`,
    "",
    `- Search with \`rg -n -i -m 5 '<words>' '${dir}'\`; try each important word and its synonyms, then read a matching file with \`sed -n '1,200p' <file>\`.`,
    "- Only read them: never change, move or delete these files or folders.",
    "- When you use one, name it (its title and date); when nothing matches, say so instead of guessing.",
    "",
  ].join("\n");
}

/** Whether keeping copies is on: the skill is in place. */
export function recallOn(home: string): boolean {
  return fs.existsSync(path.join(skillDir(home), "SKILL.md"));
}

/** Turns it on: the copies' folder (private) and the skill. */
export function enableRecall(home: string): void {
  const dir = historyDir(home);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  fs.mkdirSync(skillDir(home), { recursive: true });
  fs.writeFileSync(path.join(skillDir(home), "SKILL.md"), skillText(dir), { mode: 0o600 });
}

/** Turns it off: the skill and every copy deleted. */
export function disableRecall(home: string): void {
  fs.rmSync(skillDir(home), { recursive: true, force: true });
  fs.rmSync(historyDir(home), { recursive: true, force: true });
}

/** When each kept question was last written from, by thread id (the thread's `updatedAt`). */
export function recallIndex(home: string): Record<string, number> {
  try {
    const parsed = JSON.parse(fs.readFileSync(path.join(historyDir(home), INDEX), "utf8")) as unknown;
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([id, at]) => validThreadId(id) && typeof at === "number"));
  } catch {
    return {};
  }
}

function writeIndex(home: string, index: Record<string, number>): void {
  const file = path.join(historyDir(home), INDEX);
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(index), { mode: 0o600 });
  fs.renameSync(`${file}.tmp`, file);
}

/** Keeps one question's copy; refused when keeping copies is off or the id is not a thread's. */
export function writeRecall(home: string, thread: unknown, updatedAt: unknown, markdown: unknown): void {
  if (!recallOn(home)) throw new Error("searching earlier questions is off (Settings › Memories)");
  if (typeof thread !== "string" || !validThreadId(thread)) throw new Error("not a thread id");
  if (typeof markdown !== "string") throw new Error("the copy is text");
  const text = Buffer.byteLength(markdown) > MAX_BYTES ? `${Buffer.from(markdown).subarray(0, MAX_BYTES).toString("utf8")}\n\n(The rest of this question is not kept.)\n` : markdown;
  fs.writeFileSync(path.join(historyDir(home), `${thread}.md`), text, { mode: 0o600 });
  writeIndex(home, { ...recallIndex(home), [thread]: typeof updatedAt === "number" && Number.isFinite(updatedAt) ? updatedAt : 0 });
}

/** Drops the copies of every question not in `keep` (archived, or gone); answers how many. */
export function pruneRecall(home: string, keep: unknown): number {
  if (!recallOn(home) || !Array.isArray(keep)) return 0;
  const kept = new Set(keep.filter((id): id is string => typeof id === "string"));
  const index = recallIndex(home);
  let dropped = 0;
  for (const name of fs.readdirSync(historyDir(home))) {
    const id = name.endsWith(".md") ? name.slice(0, -3) : null;
    if (!id || !validThreadId(id) || kept.has(id)) continue;
    fs.rmSync(path.join(historyDir(home), name), { force: true });
    delete index[id];
    dropped++;
  }
  writeIndex(home, Object.fromEntries(Object.entries(index).filter(([id]) => kept.has(id))));
  return dropped;
}
