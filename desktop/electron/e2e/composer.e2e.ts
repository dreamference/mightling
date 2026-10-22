// The composer items of DESKTOP_PARITY_CLAUDE §4.6 (decided 2026-10-21), and the `!` command
// (MIGHTLING_AIRGAPPED §14.13, decided 2026-10-22), end to end: the packaged main
// process and page against the scripted stand-in for `ling app-server` (stand-in.ts). Needs a display
// and the Vite build (`npm run package` first).
//   npm run e2e -- composer
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, received, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

/** Pastes text into the composer as the system's clipboard would. */
const paste = (page: Page, text: string) => page.locator(".composer textarea").evaluate((field, value) => {
  const data = new DataTransfer();
  data.setData("text/plain", value);
  field.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
}, text);

const LOG = Array.from({ length: 60 }, (_, index) => `2026-10-21 12:${String(index).padStart(2, "0")} fetch retry ${index}`).join("\n");

test("a long paste is an attachment: a file in the question's folder in Ask, a block after the words in Work; a short one stays text", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-composer-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  const ling = writeStandIn(home, askRoot);
  const app = await launch({ args: [main], env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", MIGHTLING_HIDDEN: "1" } });
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
    const composer = page.locator(".composer textarea");

    // Ask: the chip, then a file in the new question's folder, named in what is sent.
    await paste(page, LOG);
    await expect(page.locator(".attachment", { hasText: "Pasted text · 60 lines" })).toBeVisible();
    await expect(composer).toHaveValue("");
    await composer.fill("Why does it retry so often?");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent").last()).toContainText("Done", { timeout: 15_000 });
    const asked = received(home).filter((line) => line.method === "turn/start").pop();
    const text = JSON.stringify(asked?.params);
    const file = /(\/[^"]*pasted-[^"]*\.txt)/.exec(text)?.[1];
    expect(file).toBeTruthy();
    expect(fs.readFileSync(file as string, "utf8")).toBe(LOG);
    expect(text).not.toContain("fetch retry 59");

    // A short paste is ordinary text.
    await composer.focus();
    await paste(page, "only a few words");
    await expect(page.locator(".attachment")).toHaveCount(0);

    // Work: the same chip; the text goes into the message, fenced, after the words.
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(fetcher);
    await composer.fill("");
    await paste(page, LOG);
    await expect(page.locator(".attachment", { hasText: "Pasted text · 60 lines" })).toBeVisible();
    await composer.fill("What do these retries say?");
    await page.keyboard.press("Enter");
    await expect.poll(() => JSON.stringify(received(home).filter((line) => line.method === "turn/start").pop()?.params)).toContain("What do these retries say?\\n\\nPasted text 1:\\n```\\n2026-10-21 12:00 fetch retry 0");
    expect(fs.readdirSync(fetcher).filter((name) => name.startsWith("pasted-"))).toEqual([]);
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test("a ! command runs in the user's shell at /airgapped off with its output in the thread, and is refused at on", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-shell-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  const ling = writeStandIn(home, askRoot);
  const app = await launch({ args: [main], env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", MIGHTLING_HIDDEN: "1" } });
  const shells = () => received(home).filter((line) => line.method === "thread/shellCommand").map((line) => line.params);
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    const composer = page.locator(".composer textarea");

    // Off: the command goes to the server as the user typed it, and its output shows, open, marked as the user's.
    await composer.fill("!git status");
    await page.keyboard.press("Enter");
    await expect.poll(shells).toEqual([{ threadId: "w3", command: "git status" }]);
    const shown = page.locator(".item.command.user-shell");
    await expect(shown).toContainText("nothing to commit, working tree clean");
    await expect(shown.locator(".shell-mark")).toHaveText("!");
    await expect(composer).toHaveValue("");
    // The level was asked of the launcher for that thread, here and in the thread's own folder.
    const asked = runs(home).filter((args) => args[0] === "airgapped" && args.includes("w3"));
    expect(asked.length).toBeGreaterThanOrEqual(1);

    // On: refused by the main process before it reaches the server; the words stay in the composer.
    fs.writeFileSync(path.join(home, "airgapped-on"), "");
    await composer.fill("!curl example.com");
    await page.keyboard.press("Enter");
    await expect(page.locator(".notice").last()).toContainText("airgapped is on", { timeout: 10_000 });
    await expect(composer).toHaveValue("!curl example.com");
    expect(shells()).toHaveLength(1);
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});
