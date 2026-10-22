// Several windows (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §13), end to end: the packaged
// main process and page against the scripted stand-in for `ling app-server` (stand-in.ts). New
// Window from the menu and from Ctrl+Shift+N, each window its own thread, all on one app-server; a
// thread in two windows with one composer; a link and a second `ling app` going to the most recent
// window, never to a second app; the windows back where they were at the next start; the terminal
// and the diff detached into windows of their own, live, and docked back. Needs a display and the
// Vite build (`npm run package` first).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, received, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

function scratchHome(): { home: string; ling: string } {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-windows-"));
  const askRoot = path.join(home, ".mightling", "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  return { home, ling: writeStandIn(home, askRoot) };
}

async function start(home: string, ling: string): Promise<{ app: ElectronApplication; page: Page }> {
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: path.join(home, ".mightling"), MIGHTLING_BIN: ling, MIGHTLING_ADMIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", MIGHTLING_HIDDEN: "1" },
  });
  const page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
  return { app, page };
}

const menuItem = (app: ElectronApplication, menu: string, label: string) =>
  app.evaluate(({ Menu }, [menu, label]) => {
    const top = Menu.getApplicationMenu()?.items.find((item) => item.label === menu);
    const item = top?.submenu?.items.find((entry) => entry.label === label);
    if (!item) throw new Error(`no ${label} in the ${menu} menu`);
    item.click();
  }, [menu, label] as const);

const windowCount = (app: ElectronApplication) => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().filter((window) => !window.isDestroyed()).length);

/** A message the page sends to the main process as it is, past the UI. */
const sendRaw = (page: Page, message: unknown) => page.evaluate(async (message) => {
  try {
    await (window as unknown as { electronBridge: { sendMessageFromView(m: unknown): Promise<unknown> } }).electronBridge.sendMessageFromView(message);
    return "sent";
  } catch (error) {
    return String(error);
  }
}, message);

async function openThread(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "Work", exact: true }).click();
  await page.locator(".thread", { hasText: name }).first().click();
  await expect(page.locator(".bar .title")).toHaveText(new RegExp(name));
}

test("New Window opens more windows on the one app-server, each its own thread, and a thread in two of them has one composer", async () => {
  const { home, ling } = scratchHome();
  const { app, page } = await start(home, ling);
  try {
    await openThread(page, "Compress the hero image");
    await expect(page.getByRole("textbox", { name: "Message", exact: true })).toBeVisible();

    // File › New Window: a second window, on the same view, showing no thread yet.
    const opened = app.waitForEvent("window");
    await menuItem(app, "File", "New Window");
    const second = await opened;
    await second.waitForLoadState("domcontentloaded");
    expect(second.url()).toBe("app://-/index.html#work");
    await second.locator(".thread", { hasText: "GB10" }).first().waitFor({ timeout: 30_000 }).catch(() => {});
    await openThread(second, "Fix the flaky retry test");
    await expect(page.locator(".bar .title")).toHaveText("Compress the hero image");

    // The same thread in the second window: mirrored read-only, the composer stays in the first.
    await openThread(second, "Compress the hero image");
    await expect(second.locator(".composer-mirror")).toBeVisible();
    await expect(second.getByRole("textbox", { name: "Message", exact: true })).toHaveCount(0);
    // Past the UI, the main process refuses the second window's turn on that thread.
    const refused = await sendRaw(second, { type: "work/send", message: { id: 901, method: "turn/start", params: { threadId: "w3", input: [{ type: "text", text: "two", text_elements: [] }] } } });
    expect(refused).toContain("composer of this thread is in another window");
    expect(received(home).filter((m) => m.method === "turn/start")).toHaveLength(0);

    // "Use the composer here" moves it: the second window sends, the first mirrors.
    await second.getByRole("button", { name: "Use the composer here" }).click();
    await expect(page.locator(".composer-mirror")).toBeVisible();
    const composer = second.getByRole("textbox", { name: "Message", exact: true });
    await composer.fill("Make the hero image smaller");
    await composer.press("Enter");
    await expect.poll(() => received(home).filter((m) => m.method === "turn/start" && m.params?.threadId === "w3").length).toBe(1);
    expect(await sendRaw(page, { type: "work/send", message: { id: 902, method: "turn/interrupt", params: { threadId: "w3", turnId: "x" } } })).toContain("composer of this thread is in another window");

    // Ctrl+Shift+N in a window: a third one.
    const third = app.waitForEvent("window");
    await second.locator("body").click({ position: { x: 600, y: 300 } });
    await second.keyboard.press("Control+Shift+N");
    const thirdPage = await third;
    await thirdPage.waitForLoadState("domcontentloaded");
    expect(await windowCount(app)).toBe(3);

    // A link and a second `ling app`: the most recent window shows what they name; no new window, no second app.
    await thirdPage.locator(".thread", { hasText: "GB10" }).first().waitFor({ timeout: 30_000 }).catch(() => {});
    await app.evaluate(({ app: electronApp }) => {
      electronApp.emit("second-instance", {}, ["ling-app", "ling://threads/w1"], "/", { argv: ["ling://threads/w1"] });
    });
    await expect(thirdPage.locator(".bar .title")).toHaveText("Fix the flaky retry test");
    await app.evaluate(({ app: electronApp }) => electronApp.emit("second-instance", {}, ["ling-app"], "/", { argv: [] }));
    await expect.poll(() => thirdPage.url()).toBe("app://-/index.html#");
    expect(await windowCount(app)).toBe(3);
    expect(await app.evaluate(({ app: electronApp }) => electronApp.hasSingleInstanceLock())).toBe(true);

    // Every window on the one app-server, initialised once.
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
    expect(received(home).filter((m) => m.method === "initialize")).toHaveLength(1);

    // Closing one window leaves the others; the composer it held passes to a window still showing the thread.
    await second.close();
    await expect.poll(() => windowCount(app)).toBe(2);
    await expect(page.locator(".composer-mirror")).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "Message", exact: true })).toBeVisible();
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test("the windows open at a quit come back where they were", async () => {
  const { home, ling } = scratchHome();
  const first = await start(home, ling);
  const where = [{ x: 40, y: 50, width: 900, height: 700 }, { x: 140, y: 120, width: 1000, height: 720 }];
  try {
    const opened = first.app.waitForEvent("window");
    await menuItem(first.app, "File", "New Window");
    await (await opened).waitForLoadState("domcontentloaded");
    await first.app.evaluate(({ BrowserWindow }, where) => {
      const windows = BrowserWindow.getAllWindows().sort((a, b) => a.id - b.id);
      windows.forEach((window, index) => window.setBounds(where[index]));
    }, where);
    // The session file follows a move or a resize within a moment.
    const sessionFile = path.join(home, ".local", "share", "dev.dreamference.mightling", "windows.json");
    await expect.poll(() => {
      try {
        return JSON.parse(fs.readFileSync(sessionFile, "utf8")).windows.length;
      } catch {
        return 0;
      }
    }, { timeout: 10_000 }).toBe(2);
  } finally {
    await closeApp(first.app);
  }
  const again = await start(home, ling);
  try {
    await expect.poll(() => windowCount(again.app)).toBe(2);
    const bounds = await again.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((window) => window.getNormalBounds()));
    const saved = JSON.parse(fs.readFileSync(path.join(home, ".local", "share", "dev.dreamference.mightling", "windows.json"), "utf8")).windows.map((window: { bounds: unknown }) => window.bounds);
    // Where they were, as the display allows (the same clamp as one window's bounds always had).
    for (const entry of saved) expect(bounds.some((each) => each.width === entry.width && each.height === entry.height)).toBe(true);
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(2);
  } finally {
    await closeApp(again.app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test("the terminal and the diff detach into windows of their own, stay live, and dock back when closed", async () => {
  const { home, ling } = scratchHome();
  const { app, page } = await start(home, ling);
  try {
    await openThread(page, "Fix the flaky retry test");
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    await page.keyboard.press("Control+`");
    const panel = page.getByRole("region", { name: "Bottom panel" });
    await expect(panel.locator(".xterm-rows")).toContainText("stand-in shell in");
    await page.keyboard.type("echo before the pop-out");
    await page.keyboard.press("Enter");
    await expect(panel.locator(".xterm-rows")).toContainText("before the pop-out");

    // The pane header's button: the thread's terminal in a window of its own, from its first line.
    const opened = app.waitForEvent("window");
    await panel.getByRole("button", { name: "Terminal in its own window" }).click();
    const pane = await opened;
    await pane.waitForLoadState("domcontentloaded");
    expect(pane.url()).toBe("app://-/index.html#pane=terminal&thread=w1");
    const paneRows = pane.getByRole("region", { name: "Bottom panel" }).locator(".xterm-rows");
    await expect(paneRows).toContainText("stand-in shell in", { timeout: 30_000 });
    await expect(paneRows).toContainText("before the pop-out");
    await expect(page.locator(".pane-away")).toBeVisible();
    // Live: typed in the pane, run by the same shell.
    await pane.locator(".xterm").click();
    await pane.keyboard.type("echo from the pane");
    await pane.keyboard.press("Enter");
    await expect(paneRows).toContainText("from the pane");
    // A pane has no composer: the main process refuses a turn from it.
    expect(await sendRaw(pane, { type: "work/send", message: { id: 903, method: "turn/start", params: { threadId: "w1", input: [] } } })).toContain("pane has no composer");

    // Closing the pane's window docks it: the terminal is back in the thread's window, with what was typed in the pane.
    await pane.close();
    await expect(page.locator(".pane-away")).toHaveCount(0);
    await expect(panel.locator(".xterm-rows")).toContainText("from the pane");

    // The diff, from the Window menu, and back with its Dock button.
    const diffOpened = app.waitForEvent("window");
    await menuItem(app, "Window", "Diff in Its Own Window");
    const diff = await diffOpened;
    await diff.waitForLoadState("domcontentloaded");
    expect(diff.url()).toBe("app://-/index.html#pane=review&thread=w1");
    await expect(diff.locator(".review-body")).toBeVisible({ timeout: 30_000 });
    await diff.getByRole("button", { name: "Dock the diff" }).click();
    await expect.poll(() => windowCount(app)).toBe(1);
    await expect(page.locator(".review-body")).toBeVisible();

    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});
