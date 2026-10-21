// Incognito questions (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY_CLAUDE.md §4.3, decided 2026-10-21)
// end to end: the packaged main process and page against the scripted stand-in for `ling app-server`
// (stand-in.ts). Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- incognito
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, received, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;
const MARK = ".mightling-incognito";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

/** The Ask folders marked incognito. */
const incognitoFolders = (askRoot: string) => fs.readdirSync(askRoot).filter((name) => /^q-/.test(name) && fs.existsSync(path.join(askRoot, name, MARK)));

test("an incognito question: not listed, not saved, deleted with its files when left, and when the app quits", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-incognito-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  // One left over by an app that crashed with an incognito question open: swept at the start.
  fs.mkdirSync(path.join(askRoot, "q-0ddba11"), { recursive: true });
  fs.writeFileSync(path.join(askRoot, "q-0ddba11", MARK), "");
  fs.symlinkSync("q-0ddba11", path.join(askRoot, "i-crashed"));
  const ling = writeStandIn(home, askRoot);
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", ...(out ? {} : { MIGHTLING_HIDDEN: "1" }) },
  });
  let closed = false;
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
    expect(fs.existsSync(path.join(askRoot, "q-0ddba11"))).toBe(false);
    expect(() => fs.lstatSync(path.join(askRoot, "i-crashed"))).toThrow();

    // The switch on a new question, and what it says before anything is sent.
    const toggle = page.getByRole("button", { name: "Incognito" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    const banner = page.getByRole("note");
    await expect(banner).toContainText("deleted for good");
    await page.locator(".composer textarea").fill("Explain the GB10's memory with a report");
    await page.keyboard.press("Enter");
    await expect(page.locator(".made-file", { hasText: "report.md" })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".bar .title")).toHaveText("Incognito question");
    await expect(banner).toBeVisible();
    // Started ephemeral, so the server keeps no session file; not in the sidebar; no fork, no pop-out.
    const started = received(home).find((line) => line.method === "thread/start" && line.params?.ephemeral === true);
    expect(started).toBeTruthy();
    await expect(page.locator(".thread", { hasText: "with a report" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Fork" })).toHaveCount(0);
    const [folder] = incognitoFolders(askRoot);
    expect(fs.existsSync(path.join(askRoot, folder, "report.md"))).toBe(true);
    if (out) await page.screenshot({ path: path.join(out, "incognito.png") });

    // Leaving it deletes it: the folder, its file, the link, and the server lets go of it.
    await page.getByRole("button", { name: "New question" }).click();
    await expect.poll(() => fs.existsSync(path.join(askRoot, folder))).toBe(false);
    await expect.poll(() => received(home).some((line) => line.method === "thread/unsubscribe")).toBe(true);
    expect(fs.readdirSync(askRoot).filter((name) => { try { return fs.lstatSync(path.join(askRoot, name)).isSymbolicLink() && !fs.existsSync(path.join(askRoot, name)); } catch { return false; } })).toEqual([]);
    await expect(page.getByRole("button", { name: "Incognito" })).toHaveAttribute("aria-pressed", "false");
    // A saved question is untouched.
    expect(fs.existsSync(path.join(askRoot, "q-1a2b3c"))).toBe(true);

    // One open when the app quits goes too.
    await page.getByRole("button", { name: "Incognito" }).click();
    await page.locator(".composer textarea").fill("Another question with a report");
    await page.keyboard.press("Enter");
    await expect(page.locator(".made-file", { hasText: "report.md" })).toBeVisible({ timeout: 15_000 });
    expect(incognitoFolders(askRoot)).toHaveLength(1);
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
    await closeApp(app);
    closed = true;
    expect(incognitoFolders(askRoot)).toEqual([]);
    expect(fs.existsSync(path.join(askRoot, "q-1a2b3c"))).toBe(true);
  } finally {
    if (!closed) await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});
