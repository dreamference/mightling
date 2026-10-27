// The parity backlog's B17 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): memories one by one.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-17
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B17: Settings › Memories shows what the agent remembers, file by file, and saves an edit", async () => {
  const { page, home, close } = await open({ prepare: (home) => {
    const root = path.join(home, ".mightling", "memories");
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(path.join(root, "memory_summary.md"), "## User preferences\n- Prefers tabs.\n");
    fs.writeFileSync(path.join(root, "raw_memories.md"), "not shown\n");
  } });
  try {
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Memories" }).click();
    const summary = settings.getByRole("textbox", { name: "memory_summary.md" });
    await expect(summary).toHaveValue("## User preferences\n- Prefers tabs.\n");
    await expect(settings.getByRole("textbox", { name: "raw_memories.md" })).toHaveCount(0);
    await summary.fill("## User preferences\n- Prefers spaces.\n");
    await settings.locator(".memory-file").getByRole("button", { name: "Save" }).click();
    await expect.poll(() => fs.readFileSync(path.join(home, ".mightling", "memories", "memory_summary.md"), "utf8")).toBe("## User preferences\n- Prefers spaces.\n");
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
