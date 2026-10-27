// The parity backlog's B30 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): what Ask made, in one list.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-30
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B30: Files from answers lists what Ask's questions' folders hold, and opens one beside its question", async () => {
  const { page, close } = await open({
    prepare: (home) => {
      const folder = path.join(home, ".mightling", "ask", "q-1a2b3c");
      fs.mkdirSync(folder, { recursive: true });
      fs.writeFileSync(path.join(folder, "memory.md"), "# One pool\n\nThe CPU and the GPU share it.\n");
      fs.writeFileSync(path.join(folder, ".hidden"), "x");
    },
  });
  try {
    await page.getByRole("button", { name: "Files from answers" }).click();
    const list = page.getByRole("dialog", { name: "Files from answers" });
    const question = list.getByRole("region", { name: /GB10 share memory/ });
    await expect(question.getByRole("button", { name: /memory\.md/ })).toBeVisible();
    await expect(list).not.toContainText(".hidden");
    await question.getByRole("button", { name: /memory\.md/ }).click();
    await expect(list).toHaveCount(0);
    await expect(page.getByRole("tabpanel", { name: "memory.md" })).toContainText("One pool");
    await expect(page.locator(".item.user", { hasText: "Why does the GB10 share memory with the GPU?" })).toBeVisible();
  } finally {
    await close();
  }
});
