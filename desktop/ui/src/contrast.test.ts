// Text contrast in both themes (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §3.6, item 11): every
// pair of a text token and a surface it is drawn on, read from styles.css's light (`:root`) and
// dark (`:root[data-theme="dark"]`) token sets, meets WCAG 2.2 AA, 4.5:1 for text (1.4.3; the app
// has no large-text exemption it relies on). A translucent surface (--hover, --selected) is laid
// over the surface it sits on first, as the browser composites it.
//   MIGHTLING_CONTRAST=1 npx vitest run src/contrast.test.ts   prints the table

import { describe, expect, it } from "vitest";

import css from "./styles.css?raw";

type Rgba = [number, number, number, number];

/** A token set: the custom properties of the first rule whose selector is exactly `selector`. */
function tokens(selector: string): Map<string, string> {
  const start = css.indexOf(`\n${selector} {`);
  if (start < 0) throw new Error(`${selector} is not in styles.css`);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("\n}", start));
  const found = new Map<string, string>();
  for (const match of body.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) found.set(match[1], match[2].trim());
  return found;
}

function parse(value: string): Rgba {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const digits = hex[1].length === 3 ? [...hex[1]].map((digit) => digit + digit).join("") : hex[1];
    return [0, 2, 4].map((at) => parseInt(digits.slice(at, at + 2), 16)).concat(1) as Rgba;
  }
  const rgba = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/.exec(value);
  if (rgba) return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), rgba[4] === undefined ? 1 : Number(rgba[4])];
  throw new Error(`not a colour: ${value}`);
}

/** `top` laid over the opaque `under`. */
function over(top: Rgba, under: Rgba): Rgba {
  const alpha = top[3];
  return [0, 1, 2].map((channel) => top[channel] * alpha + under[channel] * (1 - alpha)).concat(1) as Rgba;
}

function luminance([r, g, b]: Rgba): number {
  const linear = (channel: number) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

export function ratio(a: Rgba, b: Rgba): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

/**
 * Text token, surface token, and for a translucent surface the opaque one beneath it. Each pair is
 * one the stylesheet draws: the thread on the canvas, the sidebar's rows on the panel and its hover
 * and selection, menus and dialogs on --raised, code and diffs on --code-bg, chips, badges and
 * buttons on their fills, highlighted code in file tabs on the canvas.
 */
const PAIRS: [string, string, string?][] = [
  ...["--canvas", "--panel", "--raised", "--code-bg", "--accent-soft", "--notice", "--add", "--del"].map((surface) => ["--text", surface] as [string, string]),
  ["--text", "--hover", "--panel"], ["--text", "--selected", "--panel"], ["--text", "--hover", "--canvas"], ["--text", "--selected", "--canvas"],
  ...["--canvas", "--panel", "--raised", "--code-bg"].map((surface) => ["--muted", surface] as [string, string]),
  ["--muted", "--hover", "--canvas"], ["--muted", "--hover", "--panel"], ["--muted", "--selected", "--panel"], ["--muted", "--hover", "--raised"],
  ...["--canvas", "--panel", "--raised", "--code-bg"].map((surface) => ["--faint", surface] as [string, string]),
  ...["--canvas", "--panel", "--raised", "--accent-soft"].map((surface) => ["--accent-text", surface] as [string, string]),
  ["--on-accent", "--accent"], ["--on-danger", "--danger"],
  ["--danger", "--canvas"], ["--danger", "--raised"],
  ["--add-text", "--canvas"], ["--add-text", "--add"], ["--add-text", "--raised"],
  ["--del-text", "--canvas"], ["--del-text", "--del"], ["--del-text", "--raised"],
  ...["--syn-keyword", "--syn-string", "--syn-comment", "--syn-number", "--syn-title", "--syn-attr"].map((token) => [token, "--canvas"] as [string, string]),
  ["--canvas", "--text"],
];

const AA = 4.5;

/** Prints the table (the tests run under Node; the page's tsconfig has no Node types). */
const PRINT = Boolean((globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.MIGHTLING_CONTRAST);

const THEMES: Record<string, Map<string, string>> = {
  light: tokens(":root"),
  dark: new Map([...tokens(":root"), ...tokens(':root[data-theme="dark"]')]),
};

function measure(set: Map<string, string>, [text, surface, beneath]: [string, string, string?]): number {
  const read = (token: string) => {
    const value = set.get(token);
    if (!value) throw new Error(`${token} is not a token`);
    return parse(value);
  };
  let background = read(surface);
  if (background[3] < 1) background = over(background, read(beneath ?? "--canvas"));
  return ratio(over(read(text), background), background);
}

describe("text contrast, WCAG AA", () => {
  for (const [theme, set] of Object.entries(THEMES)) {
    it(`holds for every text and surface pair in the ${theme} theme`, () => {
      const rows = PAIRS.map((pair) => ({ pair: pair.filter(Boolean).join(" on "), ratio: measure(set, pair) }));
      if (PRINT) console.log(`${theme}\n${rows.map((row) => `  ${row.ratio.toFixed(2).padStart(5)}  ${row.pair}`).join("\n")}`);
      const failing = rows.filter((row) => row.ratio < AA).map((row) => `${row.pair}: ${row.ratio.toFixed(2)}`);
      expect(failing).toEqual([]);
    });
  }

  it("measures as WCAG does", () => {
    expect(ratio(parse("#000000"), parse("#ffffff"))).toBeCloseTo(21, 5);
    expect(ratio(parse("#767676"), parse("#ffffff"))).toBeCloseTo(4.54, 2);
    expect(over(parse("rgba(0, 0, 0, 0.5)"), parse("#ffffff"))[0]).toBeCloseTo(127.5, 5);
  });
});
