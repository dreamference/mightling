// Copying text from a button. `navigator.clipboard` exists only in a secure context (HTTPS or
// localhost), and `ling web` from another device on the LAN is plain HTTP by the user's decision
// (specs/DREAMFERENCE_MIGHTLING_ASK.md §4.4), so every copy button falls back to
// `document.execCommand("copy")` on a hidden textarea, which browsers still allow there.

/** What copying needs from the page; the tests pass a stand-in. */
export interface ClipboardEnv {
  navigator?: { clipboard?: { writeText(text: string): Promise<void> } };
  document?: Pick<Document, "createElement" | "execCommand"> & { body: Pick<HTMLElement, "appendChild" | "removeChild"> };
}

const pageEnv = (): ClipboardEnv => ({
  navigator: typeof navigator === "undefined" ? undefined : navigator,
  document: typeof document === "undefined" ? undefined : document,
});

/** Copies `text`; resolves true when it was copied. */
export async function copyText(text: string, env: ClipboardEnv = pageEnv()): Promise<boolean> {
  const clipboard = env.navigator?.clipboard;
  if (clipboard && typeof clipboard.writeText === "function") {
    try {
      await clipboard.writeText(text);
      return true;
    } catch {
      // A permission refusal: the old way may still work.
    }
  }
  const doc = env.document;
  if (!doc) return false;
  const area = doc.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.opacity = "0";
  doc.body.appendChild(area);
  try {
    area.select();
    return doc.execCommand("copy");
  } catch {
    return false;
  } finally {
    doc.body.removeChild(area);
  }
}
