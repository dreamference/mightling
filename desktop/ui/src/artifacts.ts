// The artifacts viewer (DESKTOP_PARITY §2.8, Phase 3 item 3): which files a tab shows rendered
// rather than as text, and how what they reference is resolved. Everything stays inside the
// window's CSP (no frames, no remote load): Markdown through the message renderer, images and
// PDFs from bytes the server read, HTML sanitised into a shadow root with no script, no form and
// no navigation. A page that should run for real goes to the preview tab (item 4).

import { parentOf } from "./files";

export type ArtifactKind = "markdown" | "image" | "pdf" | "html" | "latex" | "table" | "office";

const KINDS: Record<string, ArtifactKind> = {
  md: "markdown", markdown: "markdown", mdx: "markdown",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", avif: "image", bmp: "image", ico: "image", svg: "image",
  pdf: "pdf",
  html: "html", htm: "html", xhtml: "html",
  tex: "latex",
  csv: "table", tsv: "table",
  docx: "office", doc: "office", xlsx: "office", xls: "office", pptx: "office", ppt: "office", odt: "office", ods: "office", odp: "office", rtf: "office",
};

export const IMAGE_TYPES: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp",
  avif: "image/avif", bmp: "image/bmp", ico: "image/x-icon", svg: "image/svg+xml",
};

function extension(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? path;
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** What the viewer makes of a file; null for one shown as text only. */
export function artifactKind(path: string): ArtifactKind | null {
  return KINDS[extension(path)] ?? null;
}

/** The media type of an image file, for the blob it is shown from. */
export function imageType(path: string): string {
  return IMAGE_TYPES[extension(path)] ?? "application/octet-stream";
}

/** Whether a kind has a text source worth a Source view (an SVG does; a PNG does not). */
export function hasSource(path: string): boolean {
  const kind = artifactKind(path);
  return kind === "markdown" || kind === "html" || kind === "latex" || kind === "table" || extension(path) === "svg";
}

/** A reference that would leave the machine, or a scheme that is not a file: never loaded. */
export function isRemote(reference: string): boolean {
  return /^[a-z][a-z0-9+.-]*:/i.test(reference) && !/^file:/i.test(reference);
}

/**
 * The absolute path a document's reference (`img/a.png`, `../b.md#part`, `file:///x`) names,
 * resolved against the document's folder; null for a remote one, an in-page anchor or nothing.
 */
export function localReference(documentPath: string, reference: string): string | null {
  const bare = reference.trim().split(/[?#]/)[0];
  if (!bare || isRemote(bare)) return null;
  let path = /^file:/i.test(bare) ? decodeURIComponent(bare.replace(/^file:\/\//i, "")) : decodeURIComponent(bare);
  if (!path.startsWith("/")) path = `${parentOf(documentPath)}/${path}`;
  const parts: string[] = [];
  for (const part of path.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return `/${parts.join("/")}`;
}

/** Elements dropped from an HTML preview, with what is in them. */
const DROPPED = new Set([
  "script", "noscript", "iframe", "frame", "frameset", "object", "embed", "applet", "portal",
  "form", "input", "button", "select", "textarea", "option", "link", "meta", "base", "template",
  "audio", "video", "source", "track", "foreignobject",
]);

/** Attributes that load, navigate or act; dropped (an image's or a link's local target is kept aside). */
const ACTING = /^(on|formaction$|action$|srcset$|poster$|background$|ping$|data$|codebase$|xlink:href$|href$|src$)/i;

/**
 * Makes a parsed HTML document safe to show in the window: no script, no frame, no form, no
 * handler, nothing that loads or navigates. An image's local source is kept as `data-local` (the
 * viewer reads it through the server), a link's target as `data-href` (a local one opens in a tab).
 * Returns the number of things removed, for the note above the preview.
 */
export function sanitizeDocument(doc: Document, documentPath: string): number {
  let removed = 0;
  const walker = [...doc.querySelectorAll("*")];
  for (const element of walker) {
    if (!element.isConnected) continue;
    const name = element.localName.toLowerCase();
    if (DROPPED.has(name)) {
      element.remove();
      removed++;
      continue;
    }
    for (const attribute of [...element.attributes]) {
      const key = attribute.name.toLowerCase();
      if (!ACTING.test(key)) continue;
      // An SVG's reference to its own parts (`<use href="#leaf">`) loads nothing.
      if ((key === "href" || key === "xlink:href") && name !== "a" && attribute.value.trim().startsWith("#")) continue;
      if ((key === "src" && name === "img") || ((key === "href" || key === "xlink:href") && name === "image")) {
        const local = localReference(documentPath, attribute.value);
        if (local) element.setAttribute("data-local", local);
      } else if (key === "href" && name === "a") {
        element.setAttribute("data-href", attribute.value);
      } else {
        removed++;
      }
      element.removeAttribute(attribute.name);
    }
  }
  return removed;
}

/** Kinds the viewer opens by itself when the agent creates one (Settings › General). */
const AUTO_OPENED = new Set<ArtifactKind>(["markdown", "pdf", "html", "image"]);

/**
 * The documents and images a finished file-change item created, as absolute paths (the server's
 * paths are absolute; a relative one is the thread folder's): what "open what the agent made" opens.
 */
export function createdArtifacts(item: { type: string; status?: string; changes?: { path: string; kind: { type: string } }[] }, cwd: string | null): string[] {
  if (item.type !== "fileChange" || item.status !== "completed" || !item.changes) return [];
  return item.changes
    .filter((change) => change.kind.type === "add" && AUTO_OPENED.has(artifactKind(change.path) as ArtifactKind))
    .map((change) => (change.path.startsWith("/") || !cwd ? change.path : `${cwd.replace(/\/+$/, "")}/${change.path}`));
}

/** The most rows and columns a CSV tab shows (DESKTOP §16 B27); the rest is counted, not drawn. */
export const TABLE_ROWS = 1000;
export const TABLE_COLUMNS = 50;

/** A CSV or TSV file read as a table, capped, with how much was left out. */
export interface ParsedTable {
  rows: string[][];
  /** All the rows in the file, the header included. */
  totalRows: number;
  /** The widest row's cells. */
  totalColumns: number;
}

/** The separator a table file uses: a tab for `.tsv`; for `.csv` a comma, or a semicolon when its first line has more of those. */
export function tableSeparator(path: string, text: string): string {
  if (extension(path) === "tsv") return "\t";
  const first = text.slice(0, text.search(/\r?\n/) >= 0 ? text.search(/\r?\n/) : text.length);
  return (first.match(/;/g)?.length ?? 0) > (first.match(/,/g)?.length ?? 0) ? ";" : ",";
}

/** Splits delimited text into rows of cells, RFC 4180 quoting included (a quoted cell may hold the separator, a doubled quote and line breaks). */
export function parseTable(text: string, separator: string, maxRows = TABLE_ROWS, maxColumns = TABLE_COLUMNS): ParsedTable {
  const rows: string[][] = [];
  let totalRows = 0;
  let totalColumns = 0;
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const endRow = () => {
    row.push(cell);
    cell = "";
    totalRows++;
    totalColumns = Math.max(totalColumns, row.length);
    if (rows.length < maxRows) rows.push(row.slice(0, maxColumns));
    row = [];
  };
  const body = text.replace(/^\uFEFF/, "");
  for (let index = 0; index < body.length; index++) {
    const char = body[index];
    if (quoted) {
      if (char === '"' && body[index + 1] === '"') {
        cell += '"';
        index++;
      } else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"' && cell === "") quoted = true;
    else if (char === separator) {
      row.push(cell);
      cell = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && body[index + 1] === "\n") index++;
      endRow();
    } else cell += char;
  }
  if (cell !== "" || row.length > 0) endRow();
  return { rows, totalRows, totalColumns };
}
