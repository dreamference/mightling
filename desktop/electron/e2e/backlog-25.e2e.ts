// The parity backlog's B25 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): worktrees kept across repositories.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-25
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B25: Settings › Worktrees counts the kept worktrees per repository, or across all of them when asked", async () => {
  const { page, home, close } = await open();
  try {
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Worktrees" }).click();
    const across = settings.getByRole("checkbox", { name: /across all repositories/ });
    await expect(across).not.toBeChecked();
    await across.click();
    await expect.poll(() => received(home).some((m) => m.method === "config/value/write" && m.params?.keyPath === "desktop.mightling-worktree-keep-across-repos" && m.params?.value === true)).toBe(true);
    await expect(across).toBeChecked();
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
