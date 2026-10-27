// `/init` in Work's / menu (DESKTOP §16 B38, DESKTOP_PARITY §9.5 decision 8): a turn that writes a
// short `AGENTS.md` at the root of the repository the thread works in, from what the repository's own
// files say: its build, test and lint commands, its layout and its conventions. With one already
// there, the turn edits it where it is wrong or missing instead of replacing it, so what it proposes
// is the turn's change, in the diff, to keep or undo. Mightling's own words. Pure.

/** The file `/init` writes, at the repository's root. */
export const AGENTS_FILE = "AGENTS.md";

/** What the window knows before the turn: whether the thread's folder already has an `AGENTS.md`. */
export type InitStart = "none" | "present";

/** The folder's `AGENTS.md`, the path the window reads to choose between the two prompts. */
export function agentsPath(folder: string): string {
  return `${folder.replace(/[\\/]+$/, "")}/${AGENTS_FILE}`;
}

const LOOK = [
  "First find the repository's root: the top of its git checkout, or this folder when it is not one.",
  "Look there before writing anything: the README, the files that build and package it (package.json, pyproject.toml or setup.py, Cargo.toml, go.mod, a Makefile, CMakeLists.txt and the like), the CI workflows, the settings of its linters and formatters, its test folders, and a few source files to see how the code is written.",
  "Write only what these files show. Do not guess a command; when checking one is quick and changes nothing, you may run it.",
].join(" ");

const PARTS = [
  "The file has these parts, each brief, and leaves out any the repository says nothing about:",
  "- What the project is, in a sentence or two.",
  "- Commands: installing what it needs, building, running all the tests and a single one, linting and formatting, each as a command to copy.",
  "- Layout: the main folders and what lives in each.",
  "- Conventions: naming, style, how errors are handled, how tests are written, and whatever a newcomer would otherwise get wrong.",
  "- Rules: what must not be done (generated files not to edit by hand, folders to leave alone), where the repository says so.",
  "Keep it under about 60 lines of plain Markdown, concrete rather than general: commands and paths, not advice that fits any project.",
].join("\n");

const KEEP = [
  `If there is already an ${AGENTS_FILE} at the root, do not replace it and do not rewrite it from scratch.`,
  "Read it, keep what is still right, and change only what is wrong, out of date or missing, as small edits to the file, so that each change can be reviewed in the diff and undone.",
  "Then list the changes you made and why. If it is already right, leave it as it is and say so.",
].join(" ");

const ONLY = `Change no file but ${AGENTS_FILE}.`;

/**
 * The `/init` turn's message. `start` is what the window found in the thread's folder; the message
 * asks the agent to check the root itself all the same (the folder may be below the root). `words`,
 * what was typed after `/init`, is passed on as the user's own note.
 */
export function initPrompt(start: InitStart, words = ""): string {
  const opening = start === "present"
    ? `This repository already has an ${AGENTS_FILE}: the notes a person or a coding agent reads before working in it. Check it against the repository and propose changes to it.`
    : `Write a short ${AGENTS_FILE} for this repository: the notes a person or a coding agent reads before working in it, at the root of the repository.`;
  const note = words.trim() ? `\n\nThe user adds: ${words.trim()}` : "";
  return `${opening}\n\n${LOOK}\n\n${PARTS}\n\n${KEEP}\n\n${ONLY}${note}`;
}
