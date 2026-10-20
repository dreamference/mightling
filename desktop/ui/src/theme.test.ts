// The page's theme follower (theme.ts) against a stand-in window, and Appearance's colours per theme.

import { describe, expect, it } from "vitest";

import { DEFAULT_PREFS, appearanceFor, cleanAppearance, sanitize } from "./prefs";
import { ThemeFollower, isThemeChoice, resolveTheme, type ThemeHost } from "./theme";

function page(options: { host?: "electron" | "web"; preset?: string; dark?: boolean; stored?: unknown } = {}) {
  const system = { dark: options.dark ?? false, listeners: [] as (() => void)[] };
  const messages: ((data: unknown) => void)[] = [];
  const root = { dataset: (options.preset ? { theme: options.preset } : {}) as Record<string, string | undefined> };
  const host: ThemeHost = {
    host: options.host ?? "web",
    root,
    systemDark: () => system.dark,
    onSystemChange: (listener) => system.listeners.push(listener),
    onMessage: (listener) => messages.push(listener),
    storedChoice: () => options.stored ?? null,
  };
  return {
    host,
    root,
    setSystem(dark: boolean) {
      system.dark = dark;
      for (const listener of system.listeners) listener();
    },
    send(data: unknown) {
      for (const listener of messages) listener(data);
    },
  };
}

describe("the theme", () => {
  it("is a window preference, System by default", () => {
    expect(DEFAULT_PREFS.theme).toBe("system");
    expect(sanitize({ theme: "dark" }).theme).toBe("dark");
    expect(sanitize({ theme: "sepia" }).theme).toBe("system");
    expect(isThemeChoice("light")).toBe(true);
    expect(isThemeChoice("auto")).toBe(false);
  });

  it("resolves System to the system's theme and the others to themselves", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("in a browser, follows the system live under System and the stored choice before any render", () => {
    const window = page({ dark: true });
    const follower = new ThemeFollower(window.host);
    expect(window.root.dataset.theme).toBe("dark");
    const seen: string[] = [];
    follower.subscribe((theme) => seen.push(theme));
    window.setSystem(false);
    expect(window.root.dataset.theme).toBe("light");
    follower.setChoice("dark");
    expect(window.root.dataset.theme).toBe("dark");
    window.setSystem(true);
    window.setSystem(false);
    expect(window.root.dataset.theme).toBe("dark");
    expect(seen).toEqual(["light", "dark"]);

    const stored = page({ dark: true, stored: "light" });
    new ThemeFollower(stored.host);
    expect(stored.root.dataset.theme).toBe("light");
  });

  it("in a browser, takes no theme from window messages", () => {
    const window = page({ dark: false });
    new ThemeFollower(window.host);
    window.send({ channel: "theme", payload: "dark" });
    expect(window.root.dataset.theme).toBe("light");
  });

  it("in the desktop app, starts on the preload's theme and follows the app's word", () => {
    // The page's own query says light; the app (nativeTheme) says dark: the app's word counts.
    const window = page({ host: "electron", preset: "dark", dark: false });
    const follower = new ThemeFollower(window.host);
    expect(window.root.dataset.theme).toBe("dark");
    window.send({ channel: "theme", payload: "light" });
    expect(window.root.dataset.theme).toBe("light");
    window.send({ channel: "theme", payload: "purple" });
    window.send({ channel: "view", payload: "dark" });
    expect(window.root.dataset.theme).toBe("light");
    follower.setChoice("dark");
    expect(window.root.dataset.theme).toBe("dark");
    // Back to System: the system's theme until the app answers for the new choice.
    window.setSystem(true);
    follower.setChoice("system");
    expect(window.root.dataset.theme).toBe("dark");
    window.send({ channel: "theme", payload: "light" });
    expect(follower.current()).toBe("light");
    // Another window chose: the app's word wins over this page's older choice.
    follower.setChoice("dark");
    window.send({ channel: "theme", payload: "light" });
    expect(window.root.dataset.theme).toBe("light");
  });

  it("an unknown choice is System", () => {
    const window = page({ dark: true });
    const follower = new ThemeFollower(window.host);
    follower.setChoice("light");
    follower.setChoice(42);
    expect(window.root.dataset.theme).toBe("dark");
  });
});

describe("Appearance's colours per theme", () => {
  it("keeps a dark set of its own, colours only", () => {
    expect(cleanAppearance({ canvas: "#fff", dark: { canvas: "#101010", text: "red", accent: "#0abab5" } }))
      .toEqual({ canvas: "#fff", dark: { canvas: "#101010", accent: "#0abab5" } });
    expect(cleanAppearance({ dark: { text: "x" } })).toEqual({});
    expect(cleanAppearance({ dark: ["#000"] })).toEqual({});
  });

  it("gives each theme its own canvas and text, the fonts to both, and the light accent to dark unless it has one", () => {
    const appearance = { accent: "#ff0000", canvas: "#fafafa", text: "#111111", fontUi: "Inter", dark: { canvas: "#101010" } };
    expect(appearanceFor(appearance, "light")).toEqual({ accent: "#ff0000", canvas: "#fafafa", text: "#111111", fontUi: "Inter", fontCode: undefined });
    expect(appearanceFor(appearance, "dark")).toEqual({ accent: "#ff0000", canvas: "#101010", text: undefined, fontUi: "Inter", fontCode: undefined });
    expect(appearanceFor({ ...appearance, dark: { accent: "#00ff00" } }, "dark").accent).toBe("#00ff00");
  });
});
