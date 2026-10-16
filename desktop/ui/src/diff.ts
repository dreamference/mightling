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
