// Markdown for the agent's messages, with nothing in it that can act: raw HTML is shown as text,
// links are not followable (navigating would replace the Work window, and `target="_blank"` does
// nothing in this webview: specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §9), and images show their alt text,
// except the pictures the `image_search` tool kept on this machine, which the host serves itself at
// `/images/<id>.jpg` (specs/DREAMFERENCE_MIGHTLING_ASK.md §6): those are shown, from the same origin.
// One kind of link does act: a citation of the user's own documents, `docs:<collection>/<id>`
// (specs/DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md §8.3), which names an indexed document by its id,
// never by a path; clicking it asks the host to open that document (see `openDocument`).

import { Marked } from "marked";

const escape = (text: string): string =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** A picture the image search service stored: the only image source a message may name. */
const STORED_IMAGE = /^\/images\/[0-9a-f]{16}\.jpg$/;

/** A citation of an indexed document: `docs:documents/42`, `#page=7` for a PDF's page. */
export const DOC_LINK = /^docs:([A-Za-z0-9_-][A-Za-z0-9._-]{0,63})\/(\d{1,18})(?:#page=(\d{1,6}))?$/;

/** The document a citation link names, or null. */
export function docLink(href: string): { doc: string; page: number | null } | null {
  const found = DOC_LINK.exec(href);
  return found ? { doc: `${found[1]}/${found[2]}`, page: found[3] ? Number(found[3]) : null } : null;
}

function citation(href: string, inner: string): string | null {
  const target = docLink(href);
  if (!target) return null;
  const page = target.page === null ? "" : ` data-page="${target.page}"`;
  return `<span class="link doc-link" role="link" tabindex="0" data-doc="${escape(target.doc)}"${page} title="Open ${escape(target.doc)}">${inner}</span>`;
}

const marked = new Marked({
  gfm: true,
  breaks: false,
  async: false,
  renderer: {
    html({ text }) {
      return escape(text);
    },
    link({ href, tokens }) {
      const inner = this.parser.parseInline(tokens);
      return citation(href, inner) ?? `<span class="link" title="${escape(href)}">${inner}</span>`;
    },
    image({ href, text }) {
      if (STORED_IMAGE.test(href)) return `<img class="found-image" src="${href}" alt="${escape(text)}" title="${escape(text)}" loading="lazy" />`;
      return `<span class="image-alt">[${escape(text)}]</span>`;
    },
  },
});

/** Renders markdown to HTML that carries no script, no raw HTML, no navigation and no remote load. */
export function renderMarkdown(text: string): string {
  return marked.parse(text, { async: false }) as string;
}

/**
 * Markdown as a document in the artifacts viewer (Phase 3 item 3): the same rules as a message,
 * except that an image keeps its reference (`data-ref`) for the viewer to show a local file from
 * the server's bytes, and a link keeps its target (`data-href`) so a local one opens in a tab.
 * Nothing loads or navigates by itself; raw HTML stays text.
 */
const documentMarked = new Marked({
  gfm: true,
  breaks: false,
  async: false,
  renderer: {
    html({ text }) {
      return escape(text);
    },
    link({ href, tokens }) {
      return `<span class="link" data-href="${escape(href)}" title="${escape(href)}">${this.parser.parseInline(tokens)}</span>`;
    },
    image({ href, text }) {
      return `<img data-ref="${escape(href)}" alt="${escape(text)}">`;
    },
  },
});

export function renderDocument(text: string): string {
  return documentMarked.parse(text, { async: false }) as string;
}
