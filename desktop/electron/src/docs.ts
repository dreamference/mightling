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
