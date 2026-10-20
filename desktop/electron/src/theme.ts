// The app's theme (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §3.6, Phase 3 item 11): the
// window preference `theme` (System, Light, Dark; desktop/ui/src/prefs.json) becomes Electron's
// `nativeTheme.themeSource` in main.ts, so the system's theme, the page's `prefers-color-scheme`,
// native menus and dialogs agree; each window's own background and its title bar's symbols are the
// theme's, so nothing of the other theme shows before the page paints or in a repaint gap; and each
// window is told its theme at creation (an argument the preload reads before the first paint) and
// at every change (the `theme` message). No Electron here.

export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

/** The canvas of each theme: desktop/ui/src/styles.css's `--canvas` (theme.test.ts holds them together). */
export const CANVAS: Record<Theme, string> = { light: "#ffffff", dark: "#212121" };

/** The minimise, maximise and close symbols the title bar overlay paints (Linux and Windows). */
export const SYMBOLS: Record<Theme, string> = { light: "#1f1f1f", dark: "#ececec" };

/** The overlay's height: the page's header bar (`.bar`, 36 px). */
export const OVERLAY_HEIGHT = 36;

const ARGUMENT = "--mightling-theme=";

/** The preference as `nativeTheme.themeSource` takes it; anything else is System. */
export function themeChoice(value: unknown): ThemeChoice {
  return value === "light" || value === "dark" ? value : "system";
}

/** The window's argument naming its theme (`webPreferences.additionalArguments`). */
export function themeArgument(theme: Theme): string {
  return `${ARGUMENT}${theme}`;
}

/** The theme a window was created with, from its process's arguments; null when none is named. */
export function themeFromArgv(argv: readonly string[]): Theme | null {
  for (const argument of argv) {
    if (!argument.startsWith(ARGUMENT)) continue;
    const theme = argument.slice(ARGUMENT.length);
    if (theme === "light" || theme === "dark") return theme;
  }
  return null;
}

/** An app window's colours for `theme`: its background and its title bar overlay. */
export function windowColours(theme: Theme): { backgroundColor: string; titleBarOverlay: { color: string; symbolColor: string; height: number } } {
  return {
    backgroundColor: CANVAS[theme],
    titleBarOverlay: { color: "#00000000", symbolColor: SYMBOLS[theme], height: OVERLAY_HEIGHT },
  };
}
