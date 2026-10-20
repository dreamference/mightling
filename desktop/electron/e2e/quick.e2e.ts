// Quick Entry (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY_CLAUDE.md §4.2) end to end: the packaged
// main process and page against the scripted stand-in for `ling app-server` (stand-in.ts). A
// system-wide key cannot be pressed from here, so the test checks that the shortcut is registered
// and opens the box the way `ling://quick` does; the shortcut is an unlikely one, so the grab on the
// display the test runs on takes nothing anyone uses. Needs a display and the Vite build.
//   npm run e2e -- quick
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;
const SHORTCUT = "Super+Alt+F12";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const dataFolder = (home: string) => path.join(home, ".local", "share", "dev.dreamference.mightling");

async function open(prefs: Record<string, unknown> | null): Promise<{ app: ElectronApplication; page: Page; home: string; close: () => Promise<void> }> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-quick-"));
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
  await page.waitForLoadState("domcontentloaded");
  await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
  return {
    app, page, home,
    close: async () => {
      await closeApp(app);
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

const registered = (app: ElectronApplication, keys: string) => app.evaluate(({ globalShortcut }, accelerator) => globalShortcut.isRegistered(accelerator), keys);
/** What `ling://quick` does, as a desktop's own key binding would send it (`open-url`, the handler every platform shares). */
const openBox = (app: ElectronApplication) => app.evaluate(({ app: electronApp }) => {
  electronApp.emit("open-url", { preventDefault: () => {} }, "ling://quick");
});

test("off by default: no shortcut taken, and closing the window quits as before", async () => {
  const { app, close } = await open(null);
  try {
    expect(await registered(app, "CommandOrControl+Alt+Space")).toBe(false);
  } finally {
    await close();
  }
});

test("on: the shortcut taken, the box asks a new question in Ask at once, the window kept in the tray, Settings turns it off", async () => {
  const { app, page, home, close } = await open({ quickEntry: true, quickEntryShortcut: SHORTCUT, keepInTray: true });
  try {
    expect(await registered(app, "Super+Alt+F12")).toBe(true);
    // The box: its own window on the app's page, the question typed and sent with Enter.
    const opened = app.waitForEvent("window");
    await openBox(app);
    const box = await opened;
    await box.waitForLoadState("domcontentloaded");
    expect(box.url()).toMatch(/#quick$/);
    const field = box.getByRole("textbox", { name: "A quick question" });
    await field.fill("What is NVFP4?");
    if (out) await box.screenshot({ path: path.join(out, "4.2-quick-entry.png") });
    await field.press("Enter");
    // The app window on Ask, a new question asked with that text, its answer streamed in.
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toHaveClass(/selected/);
    await expect(page.locator(".thread", { hasText: "What is NVFP4?" })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".item.user", { hasText: "What is NVFP4?" })).toBeVisible();
    await expect(page.locator(".item.agent").last()).toContainText("Done");
    // The box is cleared for the next question.
    await expect(field).toHaveValue("");
    // Closing the window keeps the app and its window (hidden) in the tray.
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((window) => !window.webContents.getURL().endsWith("#quick"))?.close());
    await page.waitForTimeout(300);
    expect(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().filter((window) => !window.isDestroyed() && !window.webContents.getURL().endsWith("#quick")).length)).toBe(1);
    // A link (as the tray's Ask does) brings the same window back.
    await app.evaluate(({ app: electronApp }) => {
      electronApp.emit("open-url", { preventDefault: () => {} }, "ling://ask");
    });
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toHaveClass(/selected/);
    // Settings › General turns the shortcut off, and the main process lets it go.
    await page.keyboard.press("Control+,");
    const quick = page.locator("fieldset.quick-entry");
    await expect(quick).toBeVisible();
    await expect(quick.getByRole("status")).toContainText("opens the box from any app");
    await quick.getByRole("checkbox", { name: /Ask a quick question from any app/ }).uncheck();
    await expect.poll(() => registered(app, "Super+Alt+F12")).toBe(false);
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await close();
  }
});
