// The parity backlog's B43 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): summaries of finished chats.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-43
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open, prefsFile, readPrefs } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const SUMMARY = "Wanted the CSV import made faster\nDone: rows read in batches of 1000";
/** The stored summaries, none before the window first writes its preferences. */
const summaries = (home: string): Record<string, { text: string }> => (fs.existsSync(prefsFile(home)) ? (readPrefs(home).threadSummaries as Record<string, { text: string }> | undefined) ?? {} : {});
const summaryTurns = (home: string) => received(home).filter((line) => line.method === "turn/start" && String((line.params?.input as { text?: string }[] | undefined)?.[0]?.text ?? "").startsWith("Below is a conversation"));

test("B43: a chat idle for a day gets a two-line summary from the served model, shown on hover and in Search threads", async () => {
  const { page, home, close } = await open({
    env: { MIGHTLING_STAND_IN_IDLE: "1" },
    prepare: (scratch) => {
      fs.mkdirSync(path.join(scratch, ".mightling"), { recursive: true });
      fs.writeFileSync(path.join(scratch, ".mightling", "model_catalog.json"), JSON.stringify({ models: [{ slug: "qwen3.8-27b" }] }));
    },
  });
  try {
    // Nothing runs, so the two-day-old Work thread is read (never resumed) and summarized on an unkept, read-only thread.
    await expect.poll(() => summaries(home).w5?.text, { timeout: 20_000 }).toBe(SUMMARY);
    expect(received(home).find((line) => line.method === "thread/read" && line.params?.threadId === "w5")?.params).toMatchObject({ includeTurns: true });
    expect(received(home).some((line) => line.method === "thread/resume")).toBe(false);
    expect(received(home).find((line) => line.method === "thread/start")?.params).toMatchObject({ ephemeral: true, sandbox: "read-only", approvalPolicy: "never", model: "qwen3.8-27b" });
    const asked = String((summaryTurns(home)[0]?.params?.input as { text: string }[])[0].text);
    expect(asked).toContain("User: The CSV import takes a minute for 100k rows. Make it faster.");
    expect(asked).toContain("Assistant: Rows are now read in batches of 1000");
    // The question left 40 minutes ago is not summarized, and the thread is summarized once.
    expect(summaryTurns(home)).toHaveLength(1);
    expect(Object.keys(summaries(home))).toEqual(["w5"]);

    // Under its title on hover (the row's tooltip), in Work's list.
    await page.getByRole("button", { name: "Work", exact: true }).click();
    const row = page.locator(".thread", { hasText: "Speed up the CSV import" });
    await expect(row).toHaveAttribute("title", `Speed up the CSV import\n${SUMMARY}`);

    // In Search threads: found by a word only its summary has, the summary under its title.
    await page.getByRole("searchbox", { name: "Search threads" }).fill("batches");
    const found = page.locator(".project", { has: page.getByRole("heading", { name: "Found" }) });
    await expect(found.locator(".thread", { hasText: "Speed up the CSV import" })).toBeVisible();
    await expect(found.locator(".thread-summary")).toHaveText(SUMMARY);
    expect(received(home).some((line) => line.method === "thread/search")).toBe(true);
    await page.getByRole("searchbox", { name: "Search threads" }).fill("");

    // Settings › Suggested prompts turns it off (on by default): the summary is no longer shown.
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Suggested prompts" }).click();
    const switchOff = settings.getByLabel("Summarize a chat left alone for a day");
    await expect(switchOff).toBeChecked();
    await switchOff.uncheck();
    await expect.poll(() => readPrefs(home).summarizeThreads).toBe(false);
    await page.keyboard.press("Escape");
    await expect(row).toHaveAttribute("title", "Speed up the CSV import");
  } finally {
    await close();
  }
});
