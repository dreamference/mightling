// Phase 2 of DESKTOP_PARITY, end to end: the packaged main process and page against a scripted
// stand-in for `ling app-server` (stand-in.ts), one test per item, each checking what its §2 rows
// ship and that the app still runs one app-server. With MIGHTLING_LOOK_DIR set it also saves light
// pictures of each item there (the owner's screenshots), the window shown; without it the window is
// hidden. Needs a display and the Vite build (`npm run package` first).
//   MIGHTLING_LOOK_DIR=/tmp/p2 npm run e2e -- phase2
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { projectFolders, received, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");

interface Session {
  app: ElectronApplication;
  page: Page;
  home: string;
  shoot: (name: string) => Promise<void>;
  close: () => Promise<void>;
}

async function open(): Promise<Session> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-p2-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  const ling = writeStandIn(home, askRoot);
  const app = await electron.launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", ...(out ? {} : { MIGHTLING_HIDDEN: "1" }) },
  });
  const page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
  const shoot = async (name: string) => {
    if (!out) return;
    fs.mkdirSync(out, { recursive: true });
    await page.mouse.move(40, 840);
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(out, name) });
  };
  return {
    app, page, home, shoot,
    close: async () => {
      await app.close();
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

const appServers = (home: string) => runs(home).filter((args) => args.includes("app-server")).length;

test("4 composer: @ files, $ skills, / commands, the picker, follow-ups, approval keys, fork", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    const composer = page.getByRole("textbox", { name: "Message" });
    await page.locator(".approval").waitFor();

    // Enter on an empty composer approves the open approval.
    await composer.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => received(home).some((m) => m.id === "ask-1" && JSON.stringify(m.result) === JSON.stringify({ decision: "accept" }))).toBe(true);
    await expect(page.locator(".approval")).toHaveCount(0);
    // Answered once: no second answer refused by the policy.
    await expect(page.locator(".notice")).toHaveCount(0);

    // `/` at the start opens the commands.
    await composer.fill("");
    await composer.pressSequentially("/");
    await expect(page.getByRole("listbox", { name: "Commands" })).toBeVisible();
    await expect(page.getByRole("option", { name: /\/fork/ })).toBeVisible();
    await shoot("4-composer-commands.png");

    // `@` searches the thread's folder; Enter writes the path.
    await composer.fill("");
    await composer.pressSequentially("Read @cli");
    await expect(page.getByRole("listbox", { name: "Files" })).toBeVisible();
    await shoot("4-composer-mention.png");
    await page.keyboard.press("Enter");
    await expect(composer).toHaveValue("Read @fetcher/client.py ");
    expect(received(home).some((m) => m.method === "fuzzyFileSearch" && (m.params?.roots as string[])[0] === projectFolders(home).fetcher)).toBe(true);

    // `$` lists the folder's skills.
    await composer.fill("");
    await composer.pressSequentially("$re");
    await expect(page.getByRole("listbox", { name: "Skills" })).toBeVisible();
    await shoot("4-composer-skills.png");
    await page.keyboard.press("Escape");

    // The model and effort picker (Ctrl+Shift+M).
    await composer.fill("");
    await page.keyboard.press("Control+Shift+M");
    await expect(page.getByRole("menu", { name: "Model and effort" })).toBeVisible();
    await shoot("4-composer-model-picker.png");
    await page.getByRole("menuitemradio", { name: /^high/ }).click();
    await expect(page.locator(".model-chip")).toContainText("high");

    // The follow-up preference (Settings › General), then a message into a running turn waits.
    await page.keyboard.press("Control+,");
    await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
    await page.getByLabel(/Queue: it waits/).check();
    await shoot("4-settings-general.png");
    await page.keyboard.press("Escape");
    await page.locator(".thread", { hasText: "debug level" }).click();
    await expect(composer).toHaveAttribute("placeholder", /Queue a follow-up/);
    await composer.fill("Then add a line to the changelog.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".queued")).toContainText("changelog");
    expect(received(home).find((m) => m.method === "thread/queue/add")?.params).toMatchObject({ threadId: "w2" });
    await shoot("4-composer-queued.png");

    // `/fork` copies the thread.
    await composer.fill("/fork");
    await composer.press("Enter");
    await expect.poll(() => received(home).some((m) => m.method === "thread/fork" && m.params?.threadId === "w2")).toBe(true);

    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
