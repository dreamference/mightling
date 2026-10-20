// The page's theme (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §3.6, Phase 3 item 11): System,
// Light or Dark, a window preference (`theme` in prefs.json), shown as html[data-theme], which
// styles.css's two token sets key on. System follows the system's theme live.
//
// In the desktop app the main process owns the answer: it sets Electron's `nativeTheme.themeSource`
// from the preference, so the system's theme, the window's own background, the title bar's symbols
// and the native menus agree, and it sends the resolved theme over the bridge (channel `theme`) at
// every change; the preload sets html[data-theme] from the window's arguments before the first
// paint. In a browser on `ling web` the page resolves it itself, from `prefers-color-scheme` and the
// choice kept in localStorage, read synchronously before the first render; until a script runs,
// styles.css follows the system's theme by its media query.

import { useSyncExternalStore } from "react";

export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

export const THEME_CHOICES: readonly ThemeChoice[] = ["system", "light", "dark"];

/** What the page needs from its window; a stand-in in the tests. */
export interface ThemeHost {
  /** "electron" in the desktop app, "web" in a browser (bridge.ts `host`). */
  host: "electron" | "web";
  root: { dataset: Record<string, string | undefined> };
  /** The system's theme as the page sees it (`prefers-color-scheme`), with its changes. */
  systemDark: () => boolean;
  onSystemChange: (listener: () => void) => void;
  /** Messages from the app (the preload's `MessageEvent`s). */
  onMessage: (listener: (data: unknown) => void) => void;
  /** The choice a browser kept (prefs.ts's localStorage entry); null when there is none. */
  storedChoice: () => unknown;
}

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return typeof value === "string" && (THEME_CHOICES as readonly string[]).includes(value);
}

/** The theme a choice shows: its own, or for System the system's. */
export function resolveTheme(choice: ThemeChoice, systemDark: boolean): Theme {
  return choice === "system" ? (systemDark ? "dark" : "light") : choice;
}

/** Keeps html[data-theme] on the theme the choice and the system say, and tells listeners. */
export class ThemeFollower {
  private choice: ThemeChoice = "system";
  /** The desktop app's word for the theme (the preload's, then each `theme` message). */
  private fromApp: Theme | null = null;
  private readonly listeners = new Set<(theme: Theme) => void>();

  constructor(private readonly page: ThemeHost) {
    const preset = page.root.dataset.theme;
    if (page.host === "electron" && (preset === "light" || preset === "dark")) this.fromApp = preset;
    if (page.host !== "electron") {
      const stored = page.storedChoice();
      if (isThemeChoice(stored)) this.choice = stored;
    }
    page.onSystemChange(() => this.apply());
    // Only the desktop app's preload speaks on the window's messages; a browser takes none.
    if (page.host === "electron") page.onMessage((data) => {
      const message = data as { channel?: unknown; payload?: unknown } | null;
      if (!message || typeof message !== "object" || message.channel !== "theme") return;
      if (message.payload === "light" || message.payload === "dark") {
        this.fromApp = message.payload;
        this.apply();
      }
    });
    this.apply();
  }

  /** The theme shown now. In the desktop app the main process's word wins: another window may have changed the choice. */
  current(): Theme {
    const own = resolveTheme(this.choice, this.page.systemDark());
    return this.page.host === "electron" ? this.fromApp ?? own : own;
  }

  /** The user's choice, from the window's preferences; the page shows it at once. */
  setChoice(choice: unknown): void {
    const next = isThemeChoice(choice) ? choice : "system";
    if (next === this.choice) return;
    this.choice = next;
    // In the desktop app the page shows its own choice until the main process answers for it; for
    // System that is the system's theme as the page's query has it.
    if (this.page.host === "electron") this.fromApp = next === "system" ? null : next;
    this.apply();
  }

  /** Calls `listener` with each theme change; returns the unsubscribe. */
  subscribe(listener: (theme: Theme) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private apply(): void {
    const theme = this.current();
    if (this.page.root.dataset.theme === theme) return;
    this.page.root.dataset.theme = theme;
    for (const listener of this.listeners) listener(theme);
  }
}

let follower: ThemeFollower | null = null;

/** Starts following the theme for this page (main.tsx, before the first render). */
export function startTheme(page: ThemeHost): ThemeFollower {
  follower = new ThemeFollower(page);
  return follower;
}

/** The page's follower; null before main.tsx started it (the unit tests). */
export function theme(): ThemeFollower | null {
  return follower;
}

/** The theme shown now, re-rendering on each change (light before main.tsx started the follower). */
export function useShownTheme(): Theme {
  return useSyncExternalStore(
    (listener) => follower?.subscribe(listener) ?? (() => undefined),
    () => follower?.current() ?? "light",
  );
}

/** The real page's host: the document, `matchMedia`, window messages and localStorage. */
export function browserThemeHost(host: "electron" | "web", storageKey: string): ThemeHost {
  const query = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  return {
    host,
    root: document.documentElement,
    systemDark: () => query?.matches ?? false,
    onSystemChange: (listener) => query?.addEventListener?.("change", listener),
    onMessage: (listener) => window.addEventListener("message", (event) => listener(event.data)),
    storedChoice: () => {
      try {
        return (JSON.parse(localStorage.getItem(storageKey) ?? "null") as { theme?: unknown } | null)?.theme ?? null;
      } catch {
        return null;
      }
    },
  };
}
