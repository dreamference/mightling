// The parity backlog's B8 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): always allow from the approval card.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-8
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, startWork } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B8: a command's approval offers to always allow its prefix, answered with the rule", async () => {
  const { page, home, close } = await open();
  try {
    await startWork(page, home, "Run the crate's tests with a rule.");
    const card = page.locator(".approval", { hasText: "cargo test -p fetcher" });
    await expect(card.getByRole("button", { name: "Always allow crates.io" })).toBeVisible();
    await card.getByRole("button", { name: "Always allow cargo test …" }).click();
    await expect.poll(() => received(home).find((m) => m.id === "approve-rule")?.result).toEqual({ decision: { acceptWithExecpolicyAmendment: { execpolicy_amendment: ["cargo", "test"] } } });
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
