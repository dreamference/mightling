// Earlier questions for the agent (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY_CLAUDE.md §4.4, decided
// 2026-10-21) end to end: the packaged main process and page against the scripted stand-in for
// `ling app-server` (stand-in.ts). Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- recall
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("off by default; on, the skill and a copy of each Ask question (not Work's), kept up to date; off deletes them", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-recall-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  const ling = writeStandIn(home, askRoot);
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", MIGHTLING_HIDDEN: "1" },
  });
  const skill = path.join(codexHome, "skills", "earlier-questions", "SKILL.md");
  const copies = path.join(codexHome, "ask-history");
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
    expect(fs.existsSync(skill)).toBe(false);
    expect(fs.existsSync(copies)).toBe(false);

    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Memories" }).click();
    const box = settings.getByRole("checkbox", { name: "Let the agent search your earlier Ask questions" });
    await expect(box).not.toBeChecked();
    await box.check();
    // The skill names the folder; the Ask question is kept with its question and answer, Work's threads are not.
    await expect.poll(() => fs.existsSync(skill)).toBe(true);
    expect(fs.readFileSync(skill, "utf8")).toContain(copies);
    await expect.poll(() => fs.existsSync(path.join(copies, "q1.md")), { timeout: 15_000 }).toBe(true);
    const kept = fs.readFileSync(path.join(copies, "q1.md"), "utf8");
    expect(kept).toContain("# Why does the GB10 share memory with the GPU?");
    expect(kept).toContain("## Answer\n\nBecause the CPU and the GPU address **one pool** of LPDDR5X.");
    expect(fs.readdirSync(copies).filter((name) => name.endsWith(".md"))).toEqual(["q1.md"]);
    await page.keyboard.press("Escape");

    // A new question is kept once its turn ends.
    await page.locator(".composer textarea").fill("What is NVFP4?");
    await page.keyboard.press("Enter");
    await expect.poll(() => fs.readdirSync(copies).filter((name) => name.endsWith(".md")).length, { timeout: 15_000 }).toBe(2);

    // Off: the skill and every copy deleted.
    await page.keyboard.press("Control+,");
    await settings.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Memories" }).click();
    await box.uncheck();
    await expect.poll(() => fs.existsSync(skill) || fs.existsSync(copies)).toBe(false);
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});
