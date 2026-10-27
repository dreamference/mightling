// The parity backlog's B41 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): Night Shift's
// results in the inbox, unread until opened, and the archive searched.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-41
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open, prefsFile, readPrefs } from "./backlog-session";
import { projectFolders } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const RETRY = "20261020-0100-aaa";
const HINTS = "20261021-0100-bbb";
const OLD = "20260901-0100-ccc";

test("B41: finished runs unread until opened, Mark all as read, the count on Scheduled, and the archive searched", async () => {
  const { page, home, close } = await open({ prepare: (home) => {
    const night = path.join(home, ".mightling", "night");
    const { fetcher } = projectFolders(home);
    const task = (dir: string, id: string, text: string, status: string, at: string) => {
      fs.mkdirSync(path.join(dir, "tasks"), { recursive: true });
      fs.writeFileSync(path.join(dir, "tasks", `${id}.json`), JSON.stringify({ id, repo: fetcher, task: text, status, branch: `night/${id}`, test: null, on: null,
        history: [{ at, status }], result: null }));
    };
    task(night, RETRY, "Make the retry test deterministic", "done", "2026-10-20T03:10:00+01:00");
    task(night, HINTS, "Add type hints to fetcher/client.py", "stalled", "2026-10-21T03:10:00+01:00");
    task(night, "20261026-2200-ddd", "Update the changelog", "queued", "2026-10-26T22:00:00+01:00");
    fs.mkdirSync(path.join(night, "logs"), { recursive: true });
    fs.writeFileSync(path.join(night, "logs", `${RETRY}.last.txt`), "The retry test is deterministic now.\n");
    // What `ling-admin night archive` moved: the same layout under night/archive/.
    const archive = path.join(night, "archive");
    task(archive, OLD, "Rename the config loader", "done", "2026-09-01T03:10:00+01:00");
    fs.mkdirSync(path.join(archive, "logs"), { recursive: true });
    fs.writeFileSync(path.join(archive, "logs", `${OLD}.last.txt`), "The loader is load_config now.\n");
    fs.mkdirSync(path.join(archive, "reports"), { recursive: true });
    fs.writeFileSync(path.join(archive, "reports", "2026-09-01.md"), `# Night Shift — 2026-09-01\n\n## ${fetcher}\n\n### ${OLD} — done\n\n- Task: Rename the config loader\n- Tests: passed\n`);
    // A thread tie (B40) of a task archived since, and one of a task still here.
    fs.mkdirSync(path.dirname(prefsFile(home)), { recursive: true });
    fs.writeFileSync(prefsFile(home), JSON.stringify({ nightThreads: { [OLD]: "s9", [RETRY]: "s1" } }));
  } });
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    const count = page.locator(".scheduled-button .count");
    await expect(count).toHaveText("2");
    // The tie of the archived task is dropped; the other stays.
    await expect.poll(() => readPrefs(home).nightThreads).toEqual({ [RETRY]: "s1" });

    // Opening Scheduled leaves both unread; opening one run reads it.
    await page.getByRole("button", { name: /Scheduled/ }).click();
    const view = page.getByRole("dialog", { name: "Scheduled" });
    await expect(view.getByLabel("2 unread")).toBeVisible();
    await expect(view.locator("tr.night-run.unseen")).toHaveCount(2);
    await view.getByRole("button", { name: "Make the retry test deterministic" }).click();
    await expect(view.locator(".night-details")).toContainText("The retry test is deterministic now.");
    await expect(view.locator("tr.night-run.unseen")).toHaveCount(1);
    await expect(view.getByLabel("1 unread")).toBeVisible();
    await expect.poll(() => readPrefs(home).nightRead).toEqual([RETRY]);
    await page.keyboard.press("Escape");
    await expect(count).toHaveText("1");

    // Mark all as read.
    await page.getByRole("button", { name: /Scheduled/ }).click();
    await expect(view.locator("tr.night-run.unseen")).toContainText("Add type hints");
    await view.getByRole("button", { name: "Mark all as read" }).click();
    await expect(view.locator("tr.night-run.unseen")).toHaveCount(0);
    await expect(view.getByRole("button", { name: "Mark all as read" })).toHaveCount(0);
    await expect.poll(() => readPrefs(home).nightRead).toEqual([]);
    expect(typeof readPrefs(home).nightSeen).toBe("string");
    await page.keyboard.press("Escape");
    await expect(count).toHaveCount(0);

    // The archive: searched by words, a run opened on what it left, a report in full.
    await page.getByRole("button", { name: /Scheduled/ }).click();
    const archive = view.getByRole("region", { name: "Archive" });
    await expect(archive).toContainText("Rename the config loader");
    await archive.getByRole("searchbox", { name: "Search the archive" }).fill("retry");
    await expect(archive).toContainText("Nothing in the archive holds these words.");
    await archive.getByRole("searchbox", { name: "Search the archive" }).fill("config LOADER");
    await archive.getByRole("button", { name: "Rename the config loader" }).click();
    await expect(archive.locator(".night-details")).toContainText("The loader is load_config now.");
    await expect(archive.locator(".night-details")).toContainText(`Archived: the run's branch stays, night/${OLD}`);
    await archive.getByRole("button", { name: "Rename the config loader" }).click();
    await expect(archive).toContainText("- Task: Rename the config loader");
    await archive.getByRole("button", { name: "The report of 2026-09-01" }).click();
    await expect(archive.locator(".night-report")).toContainText("Tests: passed");
    // The inbox lists only what is still in the queue.
    await expect(view.locator("tr.night-run:not(.archived)")).toHaveCount(2);
  } finally {
    await close();
  }
});
