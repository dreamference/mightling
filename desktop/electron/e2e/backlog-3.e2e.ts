// The parity backlog's B3 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): the plan checklist.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-3
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, startWork } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B3: the plan checklist above the composer while the turn runs, updated in place, kept in the turn", async () => {
  const { page, home, close } = await open();
  try {
    await startWork(page, home, "Fix the retry with a plan.");
    const live = page.getByRole("region", { name: "The agent's plan" });
    await expect(live).toContainText("Read the retry loop");
    await expect(live).toContainText("0 of 3 done");
    // The second update replaces the first in place.
    await expect(live).toContainText("2 of 3 done");
    await expect(live).toContainText("The loop was simpler than it looked.");
    await expect(live.getByLabel("Run the tests: in progress")).toHaveCount(1);
    // When the turn ends it leaves the composer and stays, folded, in the turn.
    await expect(page.locator(".item.agent").last()).toContainText("Done: the retry counter");
    await expect(live).toHaveCount(0);
    const kept = page.locator(".turn details.checklist");
    await expect(kept.locator("summary")).toHaveText("Plan · 2 of 3 done");
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
