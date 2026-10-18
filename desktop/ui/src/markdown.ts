// Markdown for the agent's messages, with nothing in it that can act: raw HTML is shown as text,
// links are not followable (navigating would replace the Work window, and `target="_blank"` does
// nothing in this webview: specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §9), and images show their alt text,
// except the pictures the `image_search` tool kept on this machine, which the host serves itself at
// `/images/<id>.jpg` (specs/DREAMFERENCE_MIGHTLING_ASK.md §6): those are shown, from the same origin.

import { Marked } from "marked";

const escape = (text: string): string =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** A picture the image search service stored: the only image source a message may name. */
const STORED_IMAGE = /^\/images\/[0-9a-f]{16}\.jpg$/;

const marked = new Marked({
  gfm: true,
  breaks: false,
  async: false,
  renderer: {
    html({ text }) {
      return escape(text);
    },
    link({ href, tokens }) {
      return `<span class="link" title="${escape(href)}">${this.parser.parseInline(tokens)}</span>`;
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
