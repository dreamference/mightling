// The look's rules that a stylesheet can break without any test of behaviour noticing
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §3, §5 "Look"): every token of §3.6 is on :root,
// the dark theme (item 11, approved 2026-10-20) redefines every colour but the accent and is written
// the same way twice (for html[data-theme="dark"] and for the system's dark theme before any script
// has run), and nothing is fetched from outside the bundle (the app is air-gapped; the CSP allows
// only `'self'` fonts). Contrast is contrast.test.ts's.

import { describe, expect, it } from "vitest";

import css from "./styles.css?raw";
import interLicence from "./fonts/OFL-Inter.txt?raw";
import monoLicence from "./fonts/OFL-JetBrainsMono.txt?raw";
import readableLicence from "./fonts/OFL-AtkinsonHyperlegible.txt?raw";
import { READABLE_FONT, cleanAppearance } from "./prefs";
import iconsLicence from "./icons.LICENSE.txt?raw";

const bundledFonts = Object.keys(import.meta.glob("./fonts/*.woff2"));

/** The declarations of the first rule whose selector is exactly `selector`. */
function block(selector: string): Map<string, string> {
  const start = css.indexOf(`\n${selector} {`);
  expect(start, `${selector} in styles.css`).toBeGreaterThanOrEqual(0);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("\n}", start));
  const declarations = new Map<string, string>();
  for (const match of body.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) declarations.set(match[1], match[2].trim());
  return declarations;
}

const SPEC_TOKENS = [
  "--font-ui", "--font-code", "--size-base",
  "--canvas", "--panel", "--text", "--muted", "--line", "--accent", "--accent-soft", "--danger", "--add", "--del",
  "--accent-text", "--on-accent", "--on-danger",
  "--radius-sm", "--radius-md", "--radius-lg",
  "--motion-fast", "--motion-slow",
];

/** The stylesheet without its comments, which may name a selector no rule uses. */
// A Windows checkout may have CRLF line endings; the rules below are matched with \n.
const rules = css.replace(/\r\n/g, "\n").replace(/\/\*[\s\S]*?\*\//g, "");

describe("the look", () => {
  it("keeps every token of §3.6 on :root, with the specified radii and durations", () => {
    const root = block(":root");
    for (const token of SPEC_TOKENS) expect(root.has(token), token).toBe(true);
    expect(root.get("--radius-sm")).toBe("8px");
    expect(root.get("--radius-md")).toBe("12px");
    expect(root.get("--radius-lg")).toBe("16px");
    expect(root.get("--motion-fast")).toBe("180ms");
    expect(root.get("--motion-slow")).toBe("320ms");
    expect(root.get("--accent")).toBe("#0abab5");
  });

  it("names the bundled faces in the font stacks", () => {
    const root = block(":root");
    expect(root.get("--font-ui")).toContain("Inter");
    expect(root.get("--font-code")).toContain('"JetBrains Mono"');
  });

  it("has a light theme on :root and a dark one redefining every colour token but the accent", () => {
    expect(rules).toMatch(/:root \{[^}]*color-scheme: light;/);
    const root = block(":root");
    const dark = block(':root[data-theme="dark"]');
    const colours = [...root].filter(([, value]) => /#[0-9a-f]{3,6}\b|rgba?\(/i.test(value)).map(([token]) => token);
    expect(colours.length).toBeGreaterThan(30);
    for (const token of colours.filter((token) => token !== "--accent")) expect(dark.has(token), token).toBe(true);
    expect(dark.has("--accent")).toBe(false);
    for (const token of dark.keys()) expect(colours, token).toContain(token);
    expect(rules).toMatch(/:root\[data-theme="dark"\] \{[^}]*color-scheme: dark;/);
  });

  it("follows the system's dark theme with the same body, unless the page says light", () => {
    const body = (from: number) => rules.slice(rules.indexOf("{", from) + 1, rules.indexOf("\n}", from));
    const explicit = rules.indexOf('\n:root[data-theme="dark"] {');
    const media = rules.indexOf("@media (prefers-color-scheme: dark) {\n:root:not([data-theme=\"light\"]) {");
    expect(explicit).toBeGreaterThanOrEqual(0);
    expect(media).toBeGreaterThanOrEqual(0);
    const inner = rules.indexOf(":root:not(", media);
    expect(body(inner)).toBe(body(explicit));
    expect(rules.match(/prefers-color-scheme/g)?.length).toBe(1);
  });

  it("loads nothing from outside the bundle, and every font it names is a bundled file", () => {
    expect(css).not.toMatch(/@import/);
    expect(css).not.toMatch(/https?:\/\//);
    const urls = [...css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/g)].map((match) => match[1]);
    expect(urls.length).toBeGreaterThan(0);
    for (const url of urls) {
      expect(url).toMatch(/^\.\/fonts\/[\w.-]+\.woff2$/);
      expect(bundledFonts, url).toContain(url);
    }
  });

  it("ships the fonts' licences beside them, and the icons' beside the icons", () => {
    expect(interLicence).toContain("SIL Open Font License");
    expect(monoLicence).toContain("SIL Open Font License");
    expect(readableLicence).toContain("SIL Open Font License");
    expect(iconsLicence).toContain("ISC License");
  });

  it("paints the page's own background over the window's", () => {
    expect(css).toContain("body { background: var(--canvas); }");
    expect(block(":root").get("--canvas")).toBe("#ffffff");
  });
  it("offers a bundled readable font, a font list Appearance accepts (DESKTOP §16 B23)", () => {
    expect(css).toMatch(/font-family: "Atkinson Hyperlegible";[^}]*font-weight: 400;/);
    expect(css).toMatch(/font-family: "Atkinson Hyperlegible";[^}]*font-weight: 700;/);
    expect(cleanAppearance({ fontUi: READABLE_FONT }).fontUi).toBe(READABLE_FONT);
  });
});
