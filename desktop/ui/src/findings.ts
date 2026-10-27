// Review findings on their lines (DESKTOP_PARITY §4.5 item 27, DESKTOP §16 B12): `/review`'s answer
// lists each finding as `ling` formats it (Codex's `format_review_findings_block`):
//   - <title> — <absolute path>:<start>-<end>
//     <body, indented two spaces>
// Here they are read back, so the review pane shows each on the line it names, foldable, and sends
// it to the composer. Pure: no DOM.

import type { DiffLine, FileDiff } from "./diff";

export interface Finding {
  title: string;
  body: string;
  /** The absolute path the reviewer named. */
  path: string;
  start: number;
  end: number;
}

const HEAD = /^- (?:\[[ x]\] )?(.+?) — (.+):(\d+)-(\d+)\s*$/;

/** The findings in a review's text, in order; none when it has no findings block. */
export function parseFindings(review: string): Finding[] {
  const found: Finding[] = [];
  let current: Finding | null = null;
  const body: string[] = [];
  const close = () => {
    if (current) found.push({ ...current, body: body.join("\n").trim() });
    current = null;
    body.length = 0;
  };
  for (const line of review.split("\n")) {
    const head = line.match(HEAD);
    if (head) {
      close();
      current = { title: head[1].trim(), body: "", path: head[2].trim(), start: Number(head[3]), end: Math.max(Number(head[3]), Number(head[4])) };
      continue;
    }
    if (!current) continue;
    if (line.startsWith("  ") || line.trim() === "") body.push(line.slice(2));
    else close();
  }
  close();
  return found;
}

/** A finding's path from the repository's top, `/`-separated, or null when it lies outside `root`. */
export function repoPath(finding: Finding, root: string): string | null {
  const top = root.replace(/[\\/]+$/, "");
  const path = finding.path.replace(/\\/g, "/");
  const base = top.replace(/\\/g, "/");
  return path.startsWith(`${base}/`) ? path.slice(base.length + 1) : null;
}

/**
 * The line of `file`'s diff a finding is shown under: the first new-side line inside its range, or
 * null when none of its lines is in the diff (it is then listed above the files).
 */
export function anchorLine(file: FileDiff, finding: Finding): number | null {
  for (const hunk of file.hunks) {
    for (const line of hunk.lines as DiffLine[]) {
      if (line.newNo !== null && line.newNo >= finding.start && line.newNo <= finding.end) return line.newNo;
    }
  }
  return null;
}

/** One finding as a request for the composer. */
export function findingMessage(finding: Finding, shownPath: string): string {
  const where = finding.start === finding.end ? `${shownPath}:${finding.start}` : `${shownPath}:${finding.start}-${finding.end}`;
  return `Please fix this review finding (${where}): ${finding.title}${finding.body ? `\n\n${finding.body}` : ""}`;
}

/** The findings of the latest finished review among a thread's turns, newest turn first; none without one. */
export function latestFindings(turns: { items: { type: string; review?: string }[] }[]): Finding[] {
  for (let index = turns.length - 1; index >= 0; index--) {
    const items = turns[index].items;
    for (let at = items.length - 1; at >= 0; at--) {
      const item = items[at];
      if (item.type === "exitedReviewMode" && typeof item.review === "string") return parseFindings(item.review);
    }
  }
  return [];
}

