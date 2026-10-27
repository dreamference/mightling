// The parity backlog's B40 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): Night Shift tasks inside a chat.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-40
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open, readPrefs } from "./backlog-session";
import { projectFolders, received, runs } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

/** The id the stand-in's `ling night add` prints. */
const ID = "20261017-0930";

test("B40: a task queued from a thread shows in it as a card, then the night's result as a note, with Apply and Open the result", async () => {
  const night = (home: string) => path.join(home, ".mightling", "night");
  const writeTask = (home: string, fields: Record<string, unknown>) => fs.writeFileSync(path.join(night(home), "tasks", `${ID}.json`), JSON.stringify({
    id: ID, repo: projectFolders(home).fetcher, base: "1a2b3c4d5e", branch: `night/${ID}`, task: "Make the retry test table-driven\n\nContext: this was asked in the conversation.", test: null, on: null,
    history: [{ at: "2026-10-17T09:30:00+01:00", status: "queued" }], attempts: 0, result: null, status: "queued", ...fields,
  }));
  const { page, home, close } = await open({ prepare: (home) => {
    for (const folder of ["tasks", "logs", "reports"]) fs.mkdirSync(path.join(night(home), folder), { recursive: true });
    // `ling night add` writes the task; the stand-in only prints, so the file is here already.
    writeTask(home, {});
  } });
  try {
    const { fetcher } = projectFolders(home);
    const composer = page.locator(".composer textarea");
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(fetcher);
    await composer.fill("Make the retry test deterministic.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent")).toHaveCount(1, { timeout: 15_000 });
    const threadId = String(received(home).find((line) => line.method === "turn/start")?.params?.threadId);

    // Queued from the thread: a card in it, and the tie a window preference.
    await composer.fill("/night add Make the retry test table-driven");
    await composer.press("Enter");
    const card = page.getByRole("region", { name: "Night Shift: Make the retry test table-driven" });
    await expect(card).toContainText("Queued for tonight");
    await expect(card.locator(".night-status")).toHaveText("queued");
    await expect(card.getByRole("button", { name: "Apply" })).toHaveCount(0);
    await expect.poll(() => readPrefs(home).nightThreads).toEqual({ [ID]: threadId });
    expect(runs(home).find((args) => args[0] === "night" && args[1] === "add")?.[2]).toMatch(/^Make the retry test table-driven\n\nContext: /);

    // The night: best of 3, attempt 2 chosen, and the morning report.
    writeTask(home, { status: "done", attempts: 3, history: [{ at: "2026-10-18T03:40:00+01:00", status: "done" }],
      result: { branch: `night/${ID}`, attempts: 3, best_of: { n: 3, ran: 3, chosen: 2 } } });
    fs.writeFileSync(path.join(night(home), "logs", `${ID}-a2.last.txt`), "The retry test is table-driven now.\n");
    fs.writeFileSync(path.join(night(home), "reports", "2026-10-18.md"), [
      "# Night Shift — 2026-10-18", "", `## ${fetcher}`, "", `### ${ID} — done`, "", "- Task: Make the retry test table-driven",
      `- Branch: \`night/${ID}\`, not merged; \`ling night apply ${ID}\` brings it into your checkout`, "- Tests: `pytest -q` (detected): passed",
      "- Best of 3: 3 of 3 attempts ran; chose attempt 2 by judge", "", "## Review", "", "- Nothing was merged into your checkout.", "",
    ].join("\n"));

    // The next morning, the thread opened again: the card has the result and the report's note.
    await page.locator(".thread", { hasText: "flaky retry" }).first().click();
    await expect(card).toHaveCount(0);
    await page.locator(".thread", { hasText: "Make the retry test deterministic" }).first().click();
    await expect(card).toContainText("Best of 3: 3 ran, attempt 2 chosen");
    await expect(card).toContainText(`night/${ID}`);
    const note = card.getByRole("note", { name: "From the night's report, 2026-10-18" });
    await expect(note).toContainText("pytest -q");
    await expect(note).not.toContainText("Nothing was merged");
    // A note, never a message to the model.
    expect(received(home).filter((line) => line.method === "turn/start")).toHaveLength(1);
    expect(JSON.stringify(received(home))).not.toContain("chose attempt 2");

    // Apply: `ling night apply <id>` in the thread's folder.
    await card.getByRole("button", { name: "Apply" }).click();
    await expect(card).toContainText(`Applied night/${ID} to ${fetcher} as uncommitted changes.`);
    expect(runs(home).filter((args) => args[0] === "night" && args[1] === "apply")).toEqual([["night", "apply", ID]]);

    // Open the result: Scheduled, on that run.
    await card.getByRole("button", { name: "Open the result" }).click();
    const view = page.getByRole("dialog", { name: "Scheduled" });
    await expect(view.locator(".night-details")).toContainText("The retry test is table-driven now.");
  } finally {
    await close();
  }
});
