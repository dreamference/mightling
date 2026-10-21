// A link in an answer (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY_CLAUDE.md §2.4, decided
// 2026-10-21) end to end: the packaged main process and page against the scripted stand-in for
// `ling app-server` (stand-in.ts). The system's browser is replaced in the main process by a
// recorder, so nothing really opens. Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- links
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;
const SOURCE = "https://developer.nvidia.com/blog/nvfp4/";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("a web address in an answer: shown in full first, opened in the browser on yes, never at /airgapped on", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-links-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  const ling = writeStandIn(home, askRoot);
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", ...(out ? {} : { MIGHTLING_HIDDEN: "1" }) },
  });
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
    // The system's browser, recorded instead of opened.
    await app.evaluate(({ shell }) => {
      const opened: string[] = [];
      (globalThis as unknown as { opened: string[] }).opened = opened;
      shell.openExternal = async (url: string) => {
        opened.push(url);
      };
    });
    const opened = () => app.evaluate(() => (globalThis as unknown as { opened: string[] }).opened);

    await page.locator(".composer textarea").fill("What is NVFP4, with a source?");
    await page.keyboard.press("Enter");
    const link = page.locator(".web-link", { hasText: "NVIDIA's developer blog" });
    await expect(link).toBeVisible({ timeout: 15_000 });
    // The bare address of the source list is a link too.
    await expect(page.locator(".item.agent .web-link")).toHaveCount(2);

    // Cancel opens nothing.
    await link.click();
    const dialog = page.getByRole("dialog", { name: "Open a link" });
    await expect(dialog).toContainText("developer.nvidia.com");
    await expect(dialog.locator("code")).toHaveText(SOURCE);
    if (out) await page.screenshot({ path: path.join(out, "links-dialog.png") });
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toHaveCount(0);
    expect(await opened()).toEqual([]);
    // Esc closes it too; Enter on the focused link asks again; Open opens it.
    await link.focus();
    await page.keyboard.press("Enter");
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await link.click();
    await dialog.getByRole("button", { name: "Open in your browser" }).click();
    await expect.poll(opened).toEqual([SOURCE]);

    // Airgapped on: the dialog offers the copy only, and the main process refuses anyway.
    fs.writeFileSync(path.join(home, "airgapped-on"), "");
    await link.click();
    await expect(dialog).toContainText("Airgapped is on");
    await expect(dialog.getByRole("button", { name: "Open in your browser" })).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Copy the address" })).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    const ask = (url: string) => page.evaluate((target) => (window as unknown as { electronBridge: { sendMessageFromView(message: object): Promise<unknown> } }).electronBridge
      .sendMessageFromView({ type: "open/url", url: target, thread: null }).then(() => "opened", (error: unknown) => String(error)), url);
    expect(await ask(SOURCE)).toContain("airgapped is on");
    // Only a web address is ever opened.
    fs.rmSync(path.join(home, "airgapped-on"));
    expect(await ask("file:///etc/passwd")).toContain("only a web address");
    expect(await opened()).toEqual([SOURCE]);
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});
