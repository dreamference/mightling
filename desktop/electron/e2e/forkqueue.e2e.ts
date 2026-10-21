// Fork from here, edit in a new thread, and queue edits (DESKTOP_PARITY §4.5 item 22,
// DESKTOP_PARITY_CLAUDE §4.6) end to end: the packaged main process and page against the scripted
// stand-in for `ling app-server` (stand-in.ts). Needs a display and the Vite build.
//   npm run e2e -- forkqueue
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, received, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("a fork from the first of two turns, an edit in a new thread, and two queued messages edited and reordered", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-forkqueue-"));
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

    // Two turns in a new Work thread.
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(fetcher);
    await composer.fill("Make the retry test deterministic.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".turn")).toHaveCount(1, { timeout: 15_000 });
    await expect(page.locator(".item.agent")).toHaveCount(1);
    await composer.fill("Now log every retry.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent")).toHaveCount(2, { timeout: 15_000 });
    const first = received(home).filter((line) => line.method === "turn/start").map((line) => line.params?.threadId)[0];
    const turnIds = await page.locator(".turn").count();
    expect(turnIds).toBe(2);

    // Fork from here on the first turn: through it, the thread itself untouched.
    await page.locator(".turn").first().hover();
    await page.locator(".turn").first().getByRole("button", { name: "Fork from here" }).click();
    await expect.poll(() => received(home).filter((line) => line.method === "thread/fork").length).toBe(1);
    const fork = received(home).find((line) => line.method === "thread/fork")?.params as { threadId: string; lastTurnId?: string };
    expect(fork.threadId).toBe(first);
    expect(fork.lastTurnId).toMatch(/^turn-/);
    await expect(page.locator(".bar .title")).toContainText("(fork)");

    // Edit in a new thread on the second turn of the original: before it, its words in the new composer.
    await page.locator(".thread", { hasText: "Make the retry test deterministic" }).first().click();
    await page.locator(".turn").nth(1).hover();
    await page.locator(".turn").nth(1).getByRole("button", { name: "Edit in a new thread" }).click();
    await expect.poll(() => received(home).filter((line) => line.method === "thread/fork").length).toBe(2);
    const before = received(home).filter((line) => line.method === "thread/fork")[1]?.params as { beforeTurnId?: string; lastTurnId?: string };
    expect(before.beforeTurnId).toMatch(/^turn-/);
    expect(before.lastTurnId).toBeUndefined();
    await expect(composer).toHaveValue("Now log every retry.");

    // Two messages queued on a running thread: the first edited, the second moved up.
    await page.keyboard.press("Control+,");
    await page.getByLabel(/Queue: it waits/).check();
    await page.keyboard.press("Escape");
    await page.locator(".thread", { hasText: "debug level" }).click();
    await composer.fill("Then add a line to the changelog.");
    await page.keyboard.press("Enter");
    await composer.fill("Then run the whole suite.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".queued")).toHaveCount(2);
    await page.locator(".queued").first().getByRole("button", { name: "Edit the queued message" }).click();
    const edit = page.getByRole("textbox", { name: "The queued message" });
    await edit.fill("Then add two lines to the changelog.");
    await edit.press("Enter");
    await expect(page.locator(".queued").first()).toContainText("two lines");
    const updated = received(home).find((line) => line.method === "thread/queue/update")?.params as { input: { type: string; text?: string }[] };
    expect(updated.input).toEqual([{ type: "text", text: "Then add two lines to the changelog.", text_elements: [] }]);
    await page.locator(".queued").nth(1).getByRole("button", { name: "Move up" }).click();
    await expect(page.locator(".queued").first()).toContainText("whole suite");
    const reorder = received(home).find((line) => line.method === "thread/queue/reorder")?.params as { threadId: string; queuedSubmissionIds: string[] };
    expect(reorder.threadId).toBe("w2");
    expect(reorder.queuedSubmissionIds).toHaveLength(2);
    await expect(page.locator(".queued").first().getByRole("button", { name: "Move up" })).toBeDisabled();
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});
