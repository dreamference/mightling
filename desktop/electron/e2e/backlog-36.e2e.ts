// The parity backlog's B36 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): several threads
// chosen in the sidebar, archived, moved or deleted for good after a confirmation naming them.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-36
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open, readPrefs } from "./backlog-session";
import { projectFolders, received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const sent = (home: string, method: string) => received(home).filter((line) => line.method === method).map((line) => line.params?.threadId);

test("B36: Ctrl- and Shift-click choose threads; the bar archives, moves and deletes them for good after naming them", async () => {
  const { page, home, close } = await open();
  const { fetcher } = projectFolders(home);
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    const row = (text: string) => page.locator(".sidebar .thread-row", { hasText: text }).first();
    const bar = page.getByRole("toolbar", { name: "Chosen threads" });

    // A project to move threads into.
    await page.getByRole("button", { name: "New project" }).click();
    const dialog = page.getByRole("dialog", { name: "New project" });
    await dialog.getByRole("textbox", { name: "Project name" }).fill("Launch");
    await dialog.getByRole("textbox", { name: "Folder to add" }).fill(fetcher);
    await dialog.getByRole("textbox", { name: "Folder to add" }).press("Enter");
    await dialog.getByRole("button", { name: "Create project" }).click();
    await expect(page.locator(".server-project h2", { hasText: "Launch" })).toBeVisible();

    // Ctrl-click chooses without opening; a second Ctrl-click takes one out again.
    await row("flaky retry").locator(".thread").click({ modifiers: ["Control"] });
    await row("Compress the hero image").locator(".thread").click({ modifiers: ["Control"] });
    await expect(bar).toContainText("2 selected");
    await expect(page.locator(".sidebar .thread-row.chosen")).toHaveCount(2);
    // Not opened: the thread shown is not one of them.
    await expect(page.locator(".sidebar .thread-row.selected")).toHaveCount(0);
    await row("Compress the hero image").locator(".thread").click({ modifiers: ["Control"] });
    await expect(bar).toContainText("1 selected");

    // Shift-click: every row from the anchor (the last Ctrl-click) to the one clicked, in the order the sidebar shows them.
    await page.keyboard.press("Escape");
    await expect(bar).toHaveCount(0);
    await row("flaky retry").locator(".thread").click({ modifiers: ["Control"] });
    await row("Compress the hero image").locator(".thread").click({ modifiers: ["Shift"] });
    const shown = await page.locator(".sidebar .thread-row[data-thread-id]").evaluateAll((rows) => rows.map((entry) => (entry as HTMLElement).dataset.threadId));
    const [from, to] = [shown.indexOf("w1"), shown.indexOf("w3")].sort((a, b) => a - b);
    const range = [...new Set(shown.slice(from, to + 1))];
    expect(range.length).toBeGreaterThanOrEqual(2);
    const chosenIds = async () => (await page.locator(".sidebar .thread-row.chosen").evaluateAll((rows) => rows.map((entry) => (entry as HTMLElement).dataset.threadId))).sort();
    await expect.poll(chosenIds).toEqual([...range].sort());

    // Escape clears the choice.
    await page.keyboard.press("Escape");
    await expect(bar).toHaveCount(0);

    // Move: the chosen threads into the project, as the row's Move does one.
    await row("Log every retry").locator(".thread").click({ modifiers: ["Control"] });
    await row("Compress the hero image").locator(".thread").click({ modifiers: ["Control"] });
    await bar.getByRole("button", { name: "Move" }).click();
    await page.getByRole("menuitem", { name: "Move to project Launch" }).click();
    await expect.poll(() => received(home).filter((line) => line.method === "thread/metadata/update").map((line) => line.params).sort((a, b) => String(a?.threadId).localeCompare(String(b?.threadId))))
      .toEqual([{ threadId: "w2", projectId: "proj-1" }, { threadId: "w3", projectId: "proj-1" }]);
    await expect(bar).toHaveCount(0);
    await expect(page.locator(".server-project", { hasText: "Launch" })).toContainText("Compress the hero image");

    // Delete: the confirmation names both and how many; declined, nothing is sent and the choice stays.
    await row("Log every retry").locator(".thread").click({ modifiers: ["Control"] });
    await row("Compress the hero image").locator(".thread").click({ modifiers: ["Control"] });
    let asked = "";
    page.once("dialog", (question) => {
      asked = question.message();
      void question.dismiss();
    });
    await bar.getByRole("button", { name: "Delete…" }).click();
    await expect.poll(() => asked).toContain("Delete these 2 threads for good?");
    expect(asked).toContain("cannot be restored");
    expect(asked).toContain("• Log every retry at debug level");
    expect(asked).toContain("• Compress the hero image");
    expect(sent(home, "thread/delete")).toEqual([]);
    await expect(bar).toContainText("2 selected");
    // Accepted: both deleted, gone from the sidebar, the choice cleared.
    page.once("dialog", (question) => void question.accept());
    await bar.getByRole("button", { name: "Delete…" }).click();
    await expect.poll(() => sent(home, "thread/delete").sort()).toEqual(["w2", "w3"]);
    await expect(row("Log every retry")).toHaveCount(0);
    await expect(row("Compress the hero image")).toHaveCount(0);
    await expect(bar).toHaveCount(0);

    // One thread from its row's menu: pinned first, so its pin is forgotten with it; its subagents' threads go too.
    await row("flaky retry").hover();
    await row("flaky retry").getByRole("button", { name: "Pin" }).click();
    await expect.poll(() => readPrefs(home).pinnedThreads).toEqual(["w1"]);
    await row("flaky retry").hover();
    await row("flaky retry").getByRole("button", { name: "More" }).click();
    page.once("dialog", (question) => {
      asked = question.message();
      void question.accept();
    });
    await page.getByRole("menuitem", { name: "Delete for good…" }).click();
    await expect.poll(() => sent(home, "thread/delete").sort()).toEqual(["w1", "w2", "w3"]);
    expect(asked).toContain("Delete this thread for good?");
    expect(asked).toContain("• Fix the flaky retry test");
    await expect(row("flaky retry")).toHaveCount(0);
    await expect.poll(() => readPrefs(home).pinnedThreads).toEqual([]);

    // Ask: a question chosen and archived from the bar; Ctrl+Z brings it back.
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await page.locator(".sidebar .thread-row", { hasText: "GB10" }).locator(".thread").click({ modifiers: ["Control"] });
    await bar.getByRole("button", { name: "Archive" }).click();
    await expect.poll(() => sent(home, "thread/archive")).toEqual(["q1"]);
    await expect(page.locator(".sidebar .thread-row", { hasText: "GB10" })).toHaveCount(0);
  } finally {
    await close();
  }
});
