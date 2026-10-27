// The parity backlog's B12 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): review findings on their lines.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-12
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B12: /review's findings sit on their lines in the review pane, fold, and go to the composer", async () => {
  const { page, home, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await composer.fill("/review list the findings");
    await composer.press("Enter");
    await expect(page.locator(".review-result")).toContainText("Timeout is hard-coded");
    // The review pane, on the last turn's diff: the first finding on line 14, the other listed above the files.
    await page.keyboard.press("Control+Shift+G");
    await page.getByRole("button", { name: "Last turn" }).click();
    const onLine = page.locator(".diff-table .review-finding", { hasText: "Timeout is hard-coded" });
    await expect(onLine).toContainText("Pass the timeout in from the caller.");
    await expect(page.getByRole("region", { name: "Review findings outside this diff" })).toContainText("Backoff has no cap");
    // Folded, its body goes; unfolded, it comes back.
    await onLine.getByRole("button", { name: "Fold the finding" }).click();
    await expect(onLine).not.toContainText("Pass the timeout in from the caller.");
    await onLine.getByRole("button", { name: "Unfold the finding" }).click();
    await onLine.getByRole("button", { name: "Send to the composer" }).click();
    await expect(composer).toHaveValue(/^Please fix this review finding \(fetcher\/client\.py:14\): Timeout is hard-coded/);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
