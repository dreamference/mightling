// Ask threads (specs/DREAMFERENCE_MIGHTLING_ASK.md §3): conversations with the same agent in a
// scratch folder instead of a project. The UI only names the prompt (`ask`); the policy layer in
// `ling web` composes its text, creates the folder and confines the thread to it, so nothing here
// decides where an Ask thread works. Pure helpers, tested without a window.

import type { Thread } from "./protocol/v2/Thread";
import type { UserInput } from "./protocol/v2/UserInput";

/** The prompt an Ask thread starts with: one of the policy's `namedPrompts`. */
export const ASK_PROMPT = "ask";

/** A scratch folder's name, as `ling web` makes them (`ask/q-<hex>`, ling-rs/web/src/ask.rs). */
const SCRATCH_FOLDER = /[\\/]ask[\\/]q-[0-9a-f]+$/;

/** Whether a thread works in an Ask scratch folder: under the host's Ask root when it named one. */
export function isAskThread(thread: Pick<Thread, "cwd">, askRoot: string | null): boolean {
  const cwd = thread.cwd ?? "";
  if (askRoot) {
    const root = askRoot.replace(/[\\/]+$/, "");
    return cwd.startsWith(`${root}/`) || cwd.startsWith(`${root}\\`) ? SCRATCH_FOLDER.test(cwd) : false;
  }
  return SCRATCH_FOLDER.test(cwd);
}

/** Ask threads, most recent first; subagents and ephemeral threads are left out, as in Work. */
export function askThreads(threads: Thread[], askRoot: string | null): Thread[] {
  const recency = (thread: Thread) => thread.recencyAt ?? thread.updatedAt;
  return threads
    .filter((thread) => !thread.ephemeral && !thread.parentThreadId && isAskThread(thread, askRoot))
    .sort((a, b) => recency(b) - recency(a));
}

/** An attachment once it is in the thread's folder. */
export interface Attached {
  kind: "image" | "file";
  path: string;
  name: string;
}

/** Whether a file the user picked is sent as an image the model sees, or as a file it reads. */
export function attachmentKind(type: string, name: string): "image" | "file" {
  if (/^image\/(png|jpe?g|gif|webp)$/i.test(type)) return "image";
  return /\.(png|jpe?g|gif|webp)$/i.test(name) ? "image" : "file";
}

/**
 * A turn's input: the text, then each image as `localImage` (the model has vision); other files
 * are named in the text, so the agent reads them from its folder.
 */
export function turnInput(text: string, attached: Attached[]): UserInput[] {
  const files = attached.filter((item) => item.kind === "file");
  const note = files.length
    ? `\n\nAttached file${files.length > 1 ? "s" : ""} (in this thread's folder):\n${files.map((file) => `- ${file.path}`).join("\n")}`
    : "";
  const body = `${text}${note}`.trim();
  const inputs: UserInput[] = body ? [{ type: "text", text: body, text_elements: [] }] : [];
  for (const image of attached.filter((item) => item.kind === "image")) inputs.push({ type: "localImage", path: image.path });
  return inputs;
}

/** A pasted or picked file's name, for one without (a pasted screenshot has none). */
export function attachmentName(name: string | undefined, type: string, index: number): string {
  if (name && name.trim()) return name;
  const extension = type.split("/")[1]?.replace("jpeg", "jpg") ?? "bin";
  return `pasted-${Date.now()}-${index}.${extension}`;
}

/** A short title for a thread in the Ask list. */
export function askTitle(thread: Pick<Thread, "name" | "preview">): string {
  return thread.name || thread.preview || "New question";
}
