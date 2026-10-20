import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { CANVAS, SYMBOLS, themeArgument, themeChoice, themeFromArgv, windowColours } from "./theme";

const UI = path.join(__dirname, "..", "..", "ui", "src");
const css = fs.readFileSync(path.join(UI, "styles.css"), "utf-8");

/** `--canvas` in the first rule whose selector is exactly `selector`. */
function canvasOf(selector: string): string | undefined {
  const start = css.indexOf(`\n${selector} {`);
  return css.slice(start, css.indexOf("\n}", start)).match(/--canvas:\s*([^;]+);/)?.[1];
}

describe("the app's theme", () => {
  it("paints each window in its theme's canvas, the page's own", () => {
    expect(canvasOf(":root")).toBe(CANVAS.light);
    expect(canvasOf(':root[data-theme="dark"]')).toBe(CANVAS.dark);
    expect(windowColours("dark")).toEqual({ backgroundColor: CANVAS.dark, titleBarOverlay: { color: "#00000000", symbolColor: SYMBOLS.dark, height: 36 } });
    expect(windowColours("light").titleBarOverlay.symbolColor).toBe("#1f1f1f");
  });

  it("takes the preference as nativeTheme's source, System for anything else", () => {
    expect(themeChoice("dark")).toBe("dark");
    expect(themeChoice("light")).toBe("light");
    expect(themeChoice("system")).toBe("system");
    expect(themeChoice(undefined)).toBe("system");
    expect(themeChoice("purple")).toBe("system");
  });

  it("names a window's theme in an argument the preload reads", () => {
    expect(themeFromArgv(["/usr/lib/mightling/Mightling", "--type=renderer", themeArgument("dark")])).toBe("dark");
    expect(themeFromArgv([themeArgument("light")])).toBe("light");
    expect(themeFromArgv(["--mightling-theme=sepia"])).toBeNull();
    expect(themeFromArgv(["--type=renderer"])).toBeNull();
  });

  it("is a preference the schema knows, System by default", () => {
    const schema = JSON.parse(fs.readFileSync(path.join(UI, "prefs.json"), "utf-8")) as { keys: Record<string, { default: unknown; enum?: string[] }> };
    expect(schema.keys.theme).toEqual({ default: "system", enum: ["system", "light", "dark"] });
  });
});
