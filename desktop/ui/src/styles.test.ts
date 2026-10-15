// The look's rules that a stylesheet can break without any test of behaviour noticing
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §3, §5 "Look"): every token of §3.6 is on :root,
// the dark theme redefines every colour, and nothing is fetched from outside the bundle (the app
// is air-gapped; the CSP allows only `'self'` fonts).

import { describe, expect, it } from "vitest";

import css from "./styles.css?raw";
import interLicence from "./fonts/OFL-Inter.txt?raw";
import monoLicence from "./fonts/OFL-JetBrainsMono.txt?raw";
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
  "--radius-sm", "--radius-md", "--radius-lg",
  "--motion-fast", "--motion-slow",
];

const COLOUR = /^(#|rgba?\()/;

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

  it("redefines every colour token in the dark theme, and keeps the accent", () => {
    const light = block(":root");
    const dark = block(':root[data-theme="dark"]');
    const colours = [...light].filter(([name, value]) => COLOUR.test(value) && name !== "--accent").map(([name]) => name);
    expect(colours.length).toBeGreaterThan(10);
    for (const name of colours) expect(dark.has(name), `${name} in the dark theme`).toBe(true);
    expect(dark.has("--accent")).toBe(false);
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
    expect(iconsLicence).toContain("ISC License");
  });

  it("paints the page's own background in both themes, since the window's is transparent", () => {
    expect(css).toContain("body { background: var(--canvas); }");
    expect(block(':root[data-theme="dark"]').has("--canvas")).toBe(true);
  });
});
