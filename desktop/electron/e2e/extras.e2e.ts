// The items of DESKTOP_PARITY_CLAUDE §4 end to end: the packaged main process and page against
// the scripted stand-in for `ling app-server` (stand-in.ts). Hidden window unless MIGHTLING_LOOK_DIR
// names a folder for pictures. Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- extras
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

interface Session {
  app: ElectronApplication;
  page: Page;
  home: string;
  shoot: (name: string) => Promise<void>;
  close: () => Promise<void>;
}

async function open(extraEnv: Record<string, string> = {}): Promise<Session> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-extras-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  const ling = writeStandIn(home, askRoot);
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", ...(out ? {} : { MIGHTLING_HIDDEN: "1" }), ...extraEnv },
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
      await closeApp(app);
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

const appServers = (home: string) => runs(home).filter((args) => args.includes("app-server")).length;

test("4.1 Ask's files: a file an answer made is a card, opens beside the answer, the folder's tree; Work never shows it", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.locator(".composer textarea").fill("Explain the GB10's memory with a report");
    await page.keyboard.press("Enter");
    // The answer's file as a card, and (Settings › General: open what the agent creates) beside the answer.
    const card = page.locator(".made-file", { hasText: "report.md" });
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText("Document");
    const report = page.getByRole("tabpanel", { name: "report.md" });
    await expect(report.getByRole("heading", { name: "Memory on the GB10" })).toBeVisible();
    await shoot("4.1-ask-artifact.png");
    // Closed, the card's Open brings it back.
    await page.getByRole("button", { name: "Close the report.md tab" }).click();
    await expect(report).toHaveCount(0);
    await card.getByRole("button", { name: "Open report.md" }).click();
    await expect(report.getByRole("heading", { name: "Memory on the GB10" })).toBeVisible();
    // The header's Files shows the question's folder.
    await page.getByRole("button", { name: "Files", exact: true }).click();
    const tree = page.getByRole("navigation", { name: "Files" });
    await expect(tree.getByRole("button", { name: "report.md" })).toBeVisible();
    await shoot("4.1-ask-files.png");
    // Work does not show Ask's files, and Ask shows them again on return.
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await expect(page.getByRole("tabpanel", { name: "report.md" })).toHaveCount(0);
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await page.locator(".thread", { hasText: "with a report" }).click();
    await expect(page.getByRole("tabpanel", { name: "report.md" })).toBeVisible();
    // Another question shows none of them.
    await page.locator(".thread", { hasText: "GB10 share memory" }).click();
    await expect(page.getByRole("tabpanel", { name: "report.md" })).toHaveCount(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
