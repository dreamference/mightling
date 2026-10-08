// Markdown for the agent's messages, with nothing in it that can act: raw HTML is shown as text,
// links are not followable (navigating would replace the Work window, and `target="_blank"` does
// nothing in this webview: AGENTS.md, the desktop app), and images show their alt text.

import { Marked } from "marked";

const escape = (text: string): string =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

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
    image({ text }) {
      return `<span class="image-alt">[${escape(text)}]</span>`;
    },
  },
});

/** Renders markdown to HTML that carries no script, no raw HTML, no navigation and no remote load. */
export function renderMarkdown(text: string): string {
  return marked.parse(text, { async: false }) as string;
}
