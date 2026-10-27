// Work's pasted images (DESKTOP_PARITY §4.5 item 31, DESKTOP §16 B13): Work sends an image by its path
// (`localImage`), and a pasted screenshot has none, so the main process writes it under
// `$CODEX_HOME/attachments/<day>/`, never into the project, and answers the path. Folders older than
// two weeks are removed as new ones are written. No Electron here.

import fs from "node:fs";
import path from "node:path";

import { cleanFileName, FILE_CAP, IMAGE_CAP, privateDir, unusedPath } from "./ask";

/** How long a day's folder of attachments is kept. */
export const KEEP_DAYS = 14;

export interface WorkAttachment {
  name: string;
  kind: "image" | "file";
  /** The bytes, base64: the window's messages are JSON. */
  data: string;
}

/** The folder Work's attachments go to. */
export function attachmentsRoot(codexHome: string): string {
  return path.join(codexHome, "attachments");
}

const DAY = /^\d{8}$/;

/** Removes day folders older than `keepDays` (by their name); answers how many went. */
export function pruneAttachments(root: string, now: Date = new Date(), keepDays = KEEP_DAYS): number {
  let names: string[];
  try {
    names = fs.readdirSync(root);
  } catch {
    return 0;
  }
  const cutoff = new Date(now.getTime() - keepDays * 86_400_000).toISOString().slice(0, 10).replace(/-/g, "");
  let removed = 0;
  for (const name of names) {
    if (!DAY.test(name) || name >= cutoff) continue;
    fs.rmSync(path.join(root, name), { recursive: true, force: true });
    removed++;
  }
  return removed;
}

/** Writes one attachment for a Work turn; answers where it landed. */
export function writeWorkAttachment(codexHome: string, attachment: WorkAttachment, now: Date = new Date()): { path: string; bytes: number } {
  if (attachment.kind !== "image" && attachment.kind !== "file") throw new Error("kind is image or file");
  const cap = attachment.kind === "image" ? IMAGE_CAP : FILE_CAP;
  if (typeof attachment.data !== "string" || attachment.data.length > Math.ceil(cap / 3) * 4) throw new Error(`The limit is ${cap >> 20} MB.`);
  const bytes = Buffer.from(attachment.data, "base64");
  if (bytes.length === 0) throw new Error("The attachment is empty.");
  if (bytes.length > cap) throw new Error(`The limit is ${cap >> 20} MB.`);
  const root = attachmentsRoot(codexHome);
  pruneAttachments(root, now);
  const folder = path.join(root, now.toISOString().slice(0, 10).replace(/-/g, ""));
  privateDir(folder);
  const file = unusedPath(folder, cleanFileName(typeof attachment.name === "string" ? attachment.name : ""));
  fs.writeFileSync(file, bytes, { flag: "wx", mode: 0o600 });
  return { path: file, bytes: bytes.length };
}
