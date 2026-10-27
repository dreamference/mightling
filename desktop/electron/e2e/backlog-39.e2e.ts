// The parity backlog's B39 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): earlier versions of an edited message.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-39
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { projectFolders, received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const prefsFile = (home: string) => path.join(home, ".local", "share", "dev.dreamference.mightling", "prefs.json");
const storedVersions = (home: string) => (JSON.parse(fs.readFileSync(prefsFile(home), "utf-8")) as { messageVersions?: Record<string, string[]> }).messageVersions ?? {};

test("B39: Edit and Edit in a new thread keep the message as sent; ‹ n / m › opens each version's thread", async () => {
  const { page, home, close } = await open();
  try {
    const { fetcher } = projectFolders(home);
    const composer = page.locator(".composer textarea");
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(fetcher);
    await composer.fill("Make the retry test deterministic.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent")).toHaveCount(1, { timeout: 15_000 });
    await composer.fill("Now log every retry.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent")).toHaveCount(2, { timeout: 15_000 });
    const original = String(received(home).find((line) => line.method === "turn/start")?.params?.threadId);
    await expect(page.locator(".message-versions")).toHaveCount(0);

    // Edit in a new thread on the second message, sent changed: the second version, in the fork.
    await page.locator(".turn").nth(1).hover();
    await page.locator(".turn").nth(1).getByRole("button", { name: "Edit in a new thread" }).click();
    await expect(composer).toHaveValue("Now log every retry.");
    await composer.fill("Now log every retry at debug level.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent")).toHaveCount(2, { timeout: 15_000 });
    const versions = page.getByRole("navigation", { name: "Versions of this message" });
    await expect(versions).toHaveCount(1);
    await expect(page.locator(".turn").nth(1).getByLabel("Version 2 of 2")).toBeVisible();
    await expect(versions.getByRole("button", { name: "Next version" })).toBeDisabled();
    expect(received(home).filter((line) => line.method === "thread/fork")[0]?.params).toMatchObject({ threadId: original, beforeTurnId: expect.stringMatching(/^turn-/) });

    // The previous version opens the thread it was sent in, as it was left.
    await versions.getByRole("button", { name: "Previous version" }).click();
    await expect(page.locator(".turn").nth(1).getByLabel("Version 1 of 2")).toBeVisible();
    await expect(page.locator(".item.user").nth(1)).toHaveText("Now log every retry.");
    await expect(page.locator(".item.agent")).toHaveCount(2);

    // Edit on the last message keeps it too: a third version in a fork, nothing reverted.
    await page.locator(".turn").nth(1).getByRole("button", { name: "Edit", exact: true }).click();
    await expect(composer).toHaveValue("Now log every retry.");
    await expect.poll(() => received(home).filter((line) => line.method === "thread/fork").length).toBe(2);
    expect(received(home).filter((line) => line.method === "thread/fork")[1]?.params).toMatchObject({ threadId: original, beforeTurnId: expect.stringMatching(/^turn-/) });
    await composer.fill("Now log only the failed retries.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent")).toHaveCount(2, { timeout: 15_000 });
    await expect(page.locator(".turn").nth(1).getByLabel("Version 3 of 3")).toBeVisible();
    await expect(page.locator(".turn").first().locator(".message-versions")).toHaveCount(0);
    expect(received(home).some((line) => line.method === "thread/revert")).toBe(false);

    // Back one: the first edit's thread, its message as it was sent there.
    await versions.getByRole("button", { name: "Previous version" }).click();
    await expect(page.locator(".turn").nth(1).getByLabel("Version 2 of 3")).toBeVisible();
    await expect(page.locator(".item.user").nth(1)).toHaveText("Now log every retry at debug level.");

    // The lineage is a window preference, keyed by the first version's thread and the message's turn index.
    await expect.poll(() => Object.values(storedVersions(home))).toEqual([[original, expect.any(String), expect.any(String)]]);
    expect(Object.keys(storedVersions(home))).toEqual([`${original}:1`]);
  } finally {
    await close();
  }
});
