// The theme, end to end (DESKTOP_PARITY Phase 3 item 11, §3.6): the packaged main process and page
// against the scripted stand-in (stand-in.ts). A stored Dark is there from the first paint, the
// window's own background included; System follows the system's theme (Electron's nativeTheme, which
// the test sets as the system would); Settings › Appearance's choice is kept in the window's
// preferences and repaints every window at once. With MIGHTLING_LOOK_DIR set it also saves the key
// views in both themes there (`<view>-light.png`, `<view>-dark.png`), the window shown. Needs a
// display and the Vite build (`npm run package` first).
//   MIGHTLING_LOOK_DIR=/tmp/theme npm run e2e -- theme
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

/** The app's data folder under a scratch HOME (early-bootstrap.ts: `~/.local/share/<identifier>`). */
const dataFolder = (home: string) => path.join(home, ".local", "share", "dev.dreamference.mightling");

async function open(prefs: Record<string, unknown> | null): Promise<{ app: ElectronApplication; page: Page; home: string; close: () => Promise<void> }> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-theme-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  if (prefs) {
    fs.mkdirSync(dataFolder(home), { recursive: true });
    fs.writeFileSync(path.join(dataFolder(home), "prefs.json"), JSON.stringify(prefs));
  }
  const ling = writeStandIn(home, askRoot);
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", ...(out ? {} : { MIGHTLING_HIDDEN: "1" }) },
  });
  const page = await app.firstWindow();
  return {
    app, page, home,
    close: async () => {
      await closeApp(app);
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

const shownTheme = (page: Page) => page.evaluate(() => document.documentElement.dataset.theme);
const windowBackground = (app: ElectronApplication) => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getBackgroundColor().toLowerCase());
const storedTheme = (home: string) => (JSON.parse(fs.readFileSync(path.join(dataFolder(home), "prefs.json"), "utf-8")) as { theme?: string }).theme;

async function chooseTheme(page: Page, label: "System" | "Light" | "Dark"): Promise<void> {
  await page.keyboard.press("Control+,");
  const dialog = page.getByRole("dialog", { name: "Settings" });
  await expect(dialog).toBeVisible();
  await page.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Appearance" }).click();
  await dialog.getByRole("radiogroup", { name: "Theme" }).getByRole("radio", { name: label }).click();
  await expect(dialog.getByRole("radio", { name: label })).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
}

test("a stored Dark from the first paint, System following the system, the choice in Settings kept and painted", async () => {
  const { app, page, home, close } = await open({ theme: "dark" });
  try {
    // Before the page's script has run, the preload has set it from the window's arguments.
    await page.waitForLoadState("domcontentloaded");
    expect(await shownTheme(page)).toBe("dark");
    expect(await windowBackground(app)).toBe("#212121");
    expect(await app.evaluate(({ nativeTheme }) => nativeTheme.themeSource)).toBe("dark");
    await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(33, 33, 33)");
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe("dark");

    // System: the system's theme, live, whichever way it turns.
    await chooseTheme(page, "System");
    await expect.poll(() => storedTheme(home)).toBe("system");
    expect(await app.evaluate(({ nativeTheme }) => nativeTheme.themeSource)).toBe("system");
    await app.evaluate(({ nativeTheme }) => { nativeTheme.themeSource = "light"; });
    await expect.poll(() => shownTheme(page)).toBe("light");
    expect(await windowBackground(app)).toBe("#ffffff");
    await app.evaluate(({ nativeTheme }) => { nativeTheme.themeSource = "dark"; });
    await expect.poll(() => shownTheme(page)).toBe("dark");
    expect(await windowBackground(app)).toBe("#212121");

    // Light, whatever the system says; kept for the next start.
    await chooseTheme(page, "Light");
    await expect.poll(() => shownTheme(page)).toBe("light");
    await expect.poll(() => storedTheme(home)).toBe("light");
    expect(await windowBackground(app)).toBe("#ffffff");
    expect(await app.evaluate(({ nativeTheme }) => [nativeTheme.themeSource, nativeTheme.shouldUseDarkColors])).toEqual(["light", false]);
    expect(await page.evaluate(() => matchMedia("(prefers-color-scheme: dark)").matches)).toBe(false);

    // A pop-out opens in the theme shown and follows a change made in the main window.
    const opened = app.waitForEvent("window");
    await page.evaluate(() => (window as unknown as { electronBridge: { sendMessageFromView(message: unknown): Promise<unknown> } }).electronBridge.sendMessageFromView({ type: "window/popout", thread: "w1" }));
    const popout = await opened;
    await popout.waitForLoadState("domcontentloaded");
    expect(await shownTheme(popout)).toBe("light");
    await chooseTheme(page, "Dark");
    await expect.poll(() => shownTheme(page)).toBe("dark");
    await expect.poll(() => shownTheme(popout)).toBe("dark");
    expect(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((window) => window.getBackgroundColor().toLowerCase()))).toEqual(["#212121", "#212121"]);
  } finally {
    await close();
  }
});

/** A repository in `fetcher` with two changed files, for the review pane (as phase2.e2e.ts's). */
function scratchRepo(home: string): void {
  const root = projectFolders(home).fetcher;
  fs.writeFileSync(path.join(home, ".gitconfig"), "[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n");
  const run = (...args: string[]) => execFileSync("git", args, { cwd: root, env: { ...process.env, HOME: home, GIT_CONFIG_NOSYSTEM: "1" }, encoding: "utf8" });
  const lines = Array.from({ length: 24 }, (_, index) => `    step(${index + 1})`);
  fs.mkdirSync(path.join(root, "fetcher"), { recursive: true });
  fs.writeFileSync(path.join(root, "fetcher", "client.py"), ["def fetch(url, attempts=3):", ...lines].join("\n") + "\n");
  run("init", "-q");
  run("add", "-A");
  run("commit", "-q", "-m", "First version");
  const edited = ["def fetch(url, attempts=3):", ...lines];
  edited[2] = "    step(2)  # retried";
  edited.splice(10, 1);
  edited[20] = "    step(21)  # logged at debug level";
  fs.writeFileSync(path.join(root, "fetcher", "client.py"), edited.join("\n") + "\n");
  fs.writeFileSync(path.join(root, "fetcher", "backoff.py"), "def backoff(attempt):\n    return min(2 ** attempt, 30)\n");
}

/** A small project in `website`, for the file tree and a highlighted file tab (as phase3.e2e.ts's). */
function website(home: string): void {
  const root = projectFolders(home).website;
  fs.mkdirSync(path.join(root, "src"), { recursive: true });
  fs.writeFileSync(path.join(root, "README.md"), "# Website\n");
  fs.writeFileSync(path.join(root, "src", "hero.ts"), [
    "// The hero image, served at the width the screen asks for.",
    "export function heroSource(width: number): string {",
    "  const sizes = [640, 1280, 1920];",
    "  const fit = sizes.find((size) => size >= width) ?? sizes[sizes.length - 1];",
    "  return `/images/hero-${fit}.webp`; /* 3 sizes */",
    "}",
    "",
    "export const HERO = { alt: \"The GB10 on a desk\", lazy: true, ratio: 16 / 9 };",
    "",
  ].join("\n"));
}

for (const theme of ["light", "dark"] as const) {
  test(`the key views in the ${theme} theme`, async () => {
    test.skip(!out, "pictures only with MIGHTLING_LOOK_DIR");
    const { app, page, home, close } = await open({ theme });
    scratchRepo(home);
    website(home);
    try {
      await page.waitForLoadState("domcontentloaded");
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
      expect(await shownTheme(page)).toBe(theme);
      fs.mkdirSync(out!, { recursive: true });
      const shoot = async (name: string) => {
        await page.mouse.move(40, 840);
        await page.waitForTimeout(300);
        await page.screenshot({ path: path.join(out!, `${name}-${theme}.png`) });
      };
      await page.getByRole("button", { name: "Ask", exact: true }).click();
      await page.getByRole("button", { name: "New question" }).click();
      await shoot("ask-empty");
      await page.locator(".thread", { hasText: "GB10" }).click();
      await shoot("ask");

      // Work: a command, an edit opened onto its diff, an approval waiting.
      await page.getByRole("button", { name: "Work", exact: true }).click();
      await page.locator(".thread", { hasText: "flaky retry" }).click();
      await page.locator(".approval").waitFor();
      await page.locator(".item.files summary").first().click();
      await shoot("work");

      // The review pane, unified and split.
      await page.getByRole("button", { name: "Decline" }).click();
      await page.keyboard.press("Control+Shift+G");
      const pane = page.getByRole("complementary", { name: "Review" });
      await expect(pane.locator(".review-file")).toHaveCount(2);
      await shoot("review");
      await pane.getByRole("button", { name: "Switch the view" }).click();
      await expect(pane.locator(".diff-table.split").first()).toBeVisible();
      await shoot("review-split");
      await page.keyboard.press("Control+Shift+G");

      // The terminal in the bottom panel.
      await page.keyboard.press("Control+`");
      const panel = page.getByRole("region", { name: "Bottom panel" });
      await expect(panel.locator(".xterm-rows")).toContainText("stand-in shell in");
      await page.keyboard.type("echo the terminal follows the theme");
      await page.keyboard.press("Enter");
      await expect(panel.locator(".xterm-rows")).toContainText("the terminal follows the theme");
      await shoot("terminal");
      await page.keyboard.press("Control+`");

      // The command menu.
      await page.keyboard.press("Control+k");
      await page.getByRole("textbox", { name: "Command" }).fill("retry");
      await shoot("command-menu");
      await page.keyboard.press("Escape");

      // The file tree and a highlighted file.
      await page.locator(".thread", { hasText: "Compress the hero" }).click();
      await page.keyboard.press("Control+Shift+E");
      const tree = page.getByRole("navigation", { name: "Files" });
      await tree.getByRole("button", { name: "src" }).click();
      await tree.getByRole("button", { name: "hero.ts" }).click();
      await expect(page.getByRole("tabpanel", { name: "hero.ts" }).locator(".hljs-keyword").first()).toHaveText("export");
      await shoot("file");

      // Settings › Appearance, the theme's own page.
      await page.keyboard.press("Control+,");
      await page.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Appearance" }).click();
      await shoot("settings-appearance");
      await page.keyboard.press("Escape");
      expect(await windowBackground(app)).toBe(theme === "dark" ? "#212121" : "#ffffff");
    } finally {
      await close();
    }
  });
}
