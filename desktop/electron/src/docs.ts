// Citations of the user's own documents that open (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md
// §8.3): the window names a document by its id (`documents/42`, from a `docs:` link the local
// file index gave the model), never by a path; `ling docs locate` answers with the file only when
// it lies inside a configured collection, and the system's handler opens it.
import { execFile } from "node:child_process";
import path from "node:path";

import { command } from "./platform";

/** A collection's name and a document's number, as `ling-docs` writes them. */
export const DOCUMENT = /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}\/\d{1,18}$/;

/** The file a cited document is, from `ling docs locate <doc> --json`. */
export function locate(ling: string, doc: unknown): Promise<string> {
  if (typeof doc !== "string" || !DOCUMENT.test(doc)) return Promise.reject(new Error("not a document"));
  return new Promise((resolve, reject) => {
    const started = command(ling, ["docs", "locate", doc, "--json"]);
    execFile(started.file, started.args, { timeout: 20_000, shell: started.shell, windowsHide: true }, (error, stdout, stderr) => {
      if (error) return reject(new Error(String(stderr).trim().replace(/^ling-docs: /, "") || error.message));
      try {
        const answer = JSON.parse(String(stdout)) as { path?: unknown };
        if (typeof answer.path !== "string" || !path.isAbsolute(answer.path)) throw new Error("no path");
        resolve(answer.path);
      } catch {
        reject(new Error("the local file index gave no answer"));
      }
    });
  });
}

/** A collection's name, as `ling-docs` makes them. */
export const COLLECTION = /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$/;

function lingDocs(ling: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const started = command(ling, ["docs", ...args]);
    execFile(started.file, started.args, { timeout: 30_000, shell: started.shell, windowsHide: true, maxBuffer: 4 << 20 }, (error, stdout, stderr) => {
      const said = String(stdout).trim();
      if (error) return reject(new Error(String(stderr).trim().replace(/^ling-docs: /, "") || said || error.message));
      resolve(said);
    });
  });
}

/**
 * Settings › Your documents (LOCAL_INDEX §8.3): the collections and their state, as
 * `ling docs status --json` gives them; adding a folder, removing a collection (its index only, never
 * the files) and rebuilding one go through `ling docs` too, which applies every rule of §5.
 */
export async function docsStatus(ling: string): Promise<unknown> {
  return JSON.parse(await lingDocs(ling, ["status", "--json"])) as unknown;
}

export function docsAdd(ling: string, folder: unknown): Promise<string> {
  if (typeof folder !== "string" || !path.isAbsolute(folder) || folder.length > 4096) return Promise.reject(new Error("name a folder by its full path"));
  return lingDocs(ling, ["add", folder]);
}

/**
 * A project's knowledge folder as a collection of its own (DESKTOP_PARITY_CLAUDE §4.7), so the agent's
 * `docs_search` finds passages in it: made with its first file, indexed again when files come or go,
 * removed with its last. A `ling-docs` older than the knowledge folder's exception refuses it; the
 * files are still read as files then, so a refusal is only logged.
 */
export async function syncKnowledge(ling: string, folder: string, name: string, files: number): Promise<void> {
  if (!COLLECTION.test(name)) throw new Error("not a collection");
  if (files === 0) {
    await lingDocs(ling, ["remove", name]).catch(() => "");
    return;
  }
  await lingDocs(ling, ["add", folder, "--name", name]).catch((error: Error) => {
    if (!/already/i.test(error.message)) throw error;
    return "";
  });
  await lingDocs(ling, ["reindex", name]);
}

export function docsRemove(ling: string, name: unknown): Promise<string> {
  if (typeof name !== "string" || !COLLECTION.test(name)) return Promise.reject(new Error("not a collection"));
  return lingDocs(ling, ["remove", name]);
}

export function docsReindex(ling: string, name: unknown): Promise<string> {
  if (typeof name !== "string" || !COLLECTION.test(name)) return Promise.reject(new Error("not a collection"));
  return lingDocs(ling, ["reindex", name]);
}
