// Unified diffs as the review pane reads them (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.5,
// §3.5 "own renderer over `git diff` output"): files, hunks and numbered lines; one hunk or one file
// as a patch of its own, for `git apply` to stage, unstage or revert; the split view's rows; and the
// inline comments as the text the composer receives. Pure.

export type LineKind = "context" | "add" | "del" | "note";

export interface DiffLine {
  kind: LineKind;
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

export interface Hunk {
  header: string;
  oldStart: number;
  newStart: number;
  lines: DiffLine[];
}

export interface FileDiff {
  path: string;
  oldPath: string | null;
  status: "added" | "deleted" | "modified" | "renamed";
  /** The lines before the first hunk (`diff --git`, `index`, `---`, `+++`, modes): every patch starts with them. */
  header: string[];
  hunks: Hunk[];
  added: number;
  removed: number;
  binary: boolean;
}

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

function stripPrefix(name: string): string {
  return name.replace(/^"|"$/g, "").replace(/^[ab]\//, "").replace(/\t.*$/, "");
}

/** Parses a unified diff of one or more files (git's, or `---`/`+++` pairs without `diff --git`). */
export function parseDiff(text: string): FileDiff[] {
  const files: FileDiff[] = [];
  let file: FileDiff | null = null;
  let hunk: Hunk | null = null;
  let oldNo = 0;
  let newNo = 0;
  // Lines the current hunk still has to come, by its header: past them, a `---` line starts a file.
  let oldLeft = 0;
  let newLeft = 0;
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const inHunk: boolean = hunk !== null && (oldLeft > 0 || newLeft > 0);
    const gitFile = line.startsWith("diff --git ");
    const plainFile = !inHunk && line.startsWith("--- ") && (lines[index + 1] ?? "").startsWith("+++ ")
      && (!file || file.hunks.length > 0 || file.header.some((entry) => entry.startsWith("--- ")));
    if (gitFile || plainFile) {
      file = { path: "", oldPath: null, status: "modified", header: [], hunks: [], added: 0, removed: 0, binary: false };
      files.push(file);
      hunk = null;
    }
    if (!file) continue;
    const match: RegExpMatchArray | null = inHunk ? null : line.match(HUNK);
    if (match) {
      hunk = { header: line, oldStart: Number(match[1]), newStart: Number(match[3]), lines: [] };
      file.hunks.push(hunk);
      oldNo = hunk.oldStart;
      newNo = hunk.newStart;
      oldLeft = match[2] === undefined ? 1 : Number(match[2]);
      newLeft = match[4] === undefined ? 1 : Number(match[4]);
      continue;
    }
    if (!hunk || (!inHunk && !line.startsWith("\\"))) {
      if (hunk) {
        // Past the hunk's lines and not a new hunk: the next file's header (or trailing text).
        hunk = null;
      }
      file.header.push(line);
      if (line.startsWith("diff --git ")) {
        const names = line.slice(11).match(/^"?a\/(.+?)"? "?b\/(.+?)"?$/);
        if (names) file.path = names[2];
      } else if (line.startsWith("--- ")) {
        const name = stripPrefix(line.slice(4));
        if (name === "/dev/null") file.status = "added";
        else file.oldPath = name;
      } else if (line.startsWith("+++ ")) {
        const name = stripPrefix(line.slice(4));
        if (name === "/dev/null") file.status = "deleted";
        else file.path = name;
      } else if (line.startsWith("new file mode")) file.status = "added";
      else if (line.startsWith("deleted file mode")) file.status = "deleted";
      else if (line.startsWith("rename from ")) {
        file.status = "renamed";
        file.oldPath = line.slice(12);
      } else if (line.startsWith("rename to ")) file.path = line.slice(10);
      else if (line.startsWith("Binary files ") || line === "GIT binary patch") file.binary = true;
      continue;
    }
    if (line.startsWith("+")) {
      hunk.lines.push({ kind: "add", text: line.slice(1), oldNo: null, newNo: newNo++ });
      file.added++;
      newLeft--;
    } else if (line.startsWith("-")) {
      hunk.lines.push({ kind: "del", text: line.slice(1), oldNo: oldNo++, newNo: null });
      file.removed++;
      oldLeft--;
    } else if (line.startsWith("\\")) {
      hunk.lines.push({ kind: "note", text: line, oldNo: null, newNo: null });
    } else {
      hunk.lines.push({ kind: "context", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
      oldLeft--;
      newLeft--;
    }
  }
  for (const entry of files) {
    if (!entry.path && entry.oldPath) entry.path = entry.oldPath;
    if (entry.status === "deleted" && entry.oldPath) entry.path = entry.oldPath;
    if (entry.oldPath === entry.path && entry.status !== "renamed") entry.oldPath = null;
  }
  return files.filter((entry) => entry.path);
}

function hunkText(hunk: Hunk): string {
  const body = hunk.lines.map((line) => (line.kind === "note" ? line.text : `${line.kind === "add" ? "+" : line.kind === "del" ? "-" : " "}${line.text}`));
  return [hunk.header, ...body].join("\n");
}

/** One hunk as a patch git can apply alone (it recounts the header: `git apply --recount`). */
export function hunkPatch(file: FileDiff, index: number): string {
  const header = file.header.length ? file.header : [`--- a/${file.oldPath ?? file.path}`, `+++ b/${file.path}`];
  return [...header, hunkText(file.hunks[index])].join("\n") + "\n";
}

/**
 * Whether a hunk can be edited in place (DESKTOP_PARITY §4.5 item 16): a text file that is still there,
 * and no "\ No newline at end of file" line in the hunk, whose ending the edit could not keep.
 */
export function editable(file: FileDiff, index: number): boolean {
  const hunk = file.hunks[index];
  return Boolean(hunk) && !file.binary && file.status !== "deleted" && !hunk.lines.some((line) => line.kind === "note");
}

/** The lines the hunk covers in the file as it is now: its context and its additions, in order. */
export function hunkNewText(hunk: Hunk): string {
  return hunk.lines.filter((line) => line.kind === "context" || line.kind === "add").map((line) => line.text).join("\n");
}

/**
 * A patch that changes the hunk's lines, as the file has them now, into `edited`, for `git apply`
 * (the working tree only; `--recount` counts the lines). Its old side is the file's current text, so
 * git refuses it, and the file stays as it was, if the file changed since the diff was read. Null when
 * nothing changed. The header is a plain modification even for a new file, which exists by now.
 */
export function editPatch(file: FileDiff, index: number, edited: string): string | null {
  const hunk = file.hunks[index];
  if (!hunk || !editable(file, index)) return null;
  const before = hunkNewText(hunk);
  const after = edited.replace(/\r\n/g, "\n").replace(/\n$/, "");
  if (after === before) return null;
  const oldLines = hunk.lines.filter((line) => line.kind === "context" || line.kind === "add").map((line) => line.text);
  const newLines = after === "" ? [] : after.split("\n");
  // The lines the edit kept at either end are the patch's context, as git expects; what is left between
  // them is replaced. An edit of a hunk's first or last line leaves no context there, which git takes
  // only with --unidiff-zero (`zeroContext`); the removed lines are still checked against the file.
  let head = 0;
  while (head < oldLines.length && head < newLines.length && oldLines[head] === newLines[head]) head++;
  let tail = 0;
  while (tail < oldLines.length - head && tail < newLines.length - head && oldLines[oldLines.length - 1 - tail] === newLines[newLines.length - 1 - tail]) tail++;
  const removed = oldLines.slice(head, oldLines.length - tail);
  const added = newLines.slice(head, newLines.length - tail);
  // A range of no lines starts at the line before it (git's own rule): a hunk that only took lines out
  // already has its start there, so lines written into it go after that line.
  const start = oldLines.length ? hunk.newStart : hunk.newStart + 1;
  const header = `@@ -${oldLines.length ? start : start - 1},${oldLines.length} +${newLines.length ? start : start - 1},${newLines.length} @@`;
  return [
    `diff --git a/${file.path} b/${file.path}`,
    `--- a/${file.path}`,
    `+++ b/${file.path}`,
    header,
    ...oldLines.slice(0, head).map((line) => ` ${line}`),
    ...removed.map((line) => `-${line}`),
    ...added.map((line) => `+${line}`),
    ...oldLines.slice(oldLines.length - tail).map((line) => ` ${line}`),
  ].join("\n") + "\n";
}

/** Whether an edit patch has no context line before or after its change, which `git apply` takes only with --unidiff-zero. */
export function zeroContext(patch: string): boolean {
  const body = patch.split("\n").filter((line) => /^[ +-]/.test(line) && !/^(---|\+\+\+) /.test(line));
  return body.length > 0 && (!body[0].startsWith(" ") || !body[body.length - 1].startsWith(" "));
}

/** The whole file as a patch. */
export function filePatch(file: FileDiff): string {
  const header = file.header.length ? file.header : [`--- a/${file.oldPath ?? file.path}`, `+++ b/${file.path}`];
  return [...header, ...file.hunks.map(hunkText)].join("\n") + "\n";
}

/** The split view's rows: removals beside the additions that replace them, context on both sides. */
export function splitRows(hunk: Hunk): { left: DiffLine | null; right: DiffLine | null }[] {
  const rows: { left: DiffLine | null; right: DiffLine | null }[] = [];
  let dels: DiffLine[] = [];
  let adds: DiffLine[] = [];
  const flush = () => {
    for (let i = 0; i < Math.max(dels.length, adds.length); i++) rows.push({ left: dels[i] ?? null, right: adds[i] ?? null });
    dels = [];
    adds = [];
  };
  for (const line of hunk.lines) {
    if (line.kind === "del") {
      if (adds.length) flush();
      dels.push(line);
    } else if (line.kind === "add") adds.push(line);
    else {
      flush();
      rows.push({ left: line, right: line });
    }
  }
  flush();
  return rows;
}

/** A fingerprint of a file's changes: a "viewed" mark holds until they change. */
export function fingerprint(file: FileDiff): string {
  let hash = 5381;
  const text = file.path + file.hunks.map(hunkText).join("\n");
  for (let i = 0; i < text.length; i++) hash = ((hash << 5) + hash + text.charCodeAt(i)) | 0;
  return `${file.path}:${(hash >>> 0).toString(36)}`;
}

export interface ReviewComment {
  path: string;
  /** The line in the new file, or in the old one for a removed line. */
  line: number;
  side: "new" | "old";
  quote: string;
  text: string;
}

/** The comments as review guidance for the composer, one per line, each with the code it is about. */
export function commentsMessage(comments: ReviewComment[]): string {
  if (comments.length === 0) return "";
  const items = comments.map((comment) => `- ${comment.path}:${comment.line}${comment.side === "old" ? " (removed line)" : ""} \`${comment.quote.trim().slice(0, 120)}\`: ${comment.text.trim()}`);
  return `Please address these review comments:\n${items.join("\n")}`;
}

/**
 * A turn's diff with its paths moved under `directory` (a folder inside the repository, `/`
 * separated): the agent names paths from the thread's folder, git applies them from the
 * repository's top. `git apply --directory` does the same.
 */
export function underDirectory(diff: string, directory: string): string {
  const dir = directory.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!dir) return diff;
  // The ---/+++ lines name files only before a file's first hunk: in a hunk they are changed lines.
  let header = true;
  return diff.split("\n").map((line) => {
    const names = /^diff --git a\/(.+) b\/(.+)$/.exec(line);
    if (names) {
      header = true;
      return `diff --git a/${dir}/${names[1]} b/${dir}/${names[2]}`;
    }
    if (line.startsWith("@@")) header = false;
    const side = header ? /^(---|\+\+\+) ([ab])\/(.+)$/.exec(line) : null;
    if (side) return `${side[1]} ${side[2]}/${dir}/${side[3]}`;
    return line;
  }).join("\n");
}

/** What Revert's confirmation needs to know of the turns it removes (a `TurnView`'s fields). */
export interface RevertedTurn {
  diff: string | null;
  items: { type: string }[];
}

/**
 * Revert's question for `turns[index]`, which removes it and every later turn and undoes its diff.
 * The diff holds only the edits the agent made with its own tools: what a shell command it ran
 * wrote is in no diff, so the question says so when any removed turn ran one (`commandExecution`).
 */
export function revertQuestion(turns: RevertedTurn[], index: number): string {
  const later = turns.length - 1 - index;
  const removed = turns.slice(index);
  const what = `${later > 0 ? `this turn and the ${later} after it` : "this turn"} from the thread`;
  const files = turns[index]?.diff?.trim() ? " and undo the file changes it made" : "; it made no file changes this window knows of, so files stay as they are";
  const shell = removed.some((turn) => turn.items.some((item) => item.type === "commandExecution"))
    ? ` Changes made by shell commands in ${later > 0 ? "these turns" : "this turn"} are not undone.`
    : "";
  return `Remove ${what}${files}?${shell} The conversation cannot be brought back from here.`;
}
