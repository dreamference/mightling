// The parity backlog's B11 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): tool calls in full.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-11
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, startWork } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B11: an MCP tool call shows its arguments, progress and result, folded under its name", async () => {
  const { page, home, close } = await open();
  try {
    await startWork(page, home, "File the bug with a tool.");
    const call = page.locator(".item.tool", { hasText: "tickets · create_ticket" });
    await expect(call).toContainText("1.2 s");
    await call.locator("summary").click();
    await expect(call.locator(".tool-part", { hasText: "Arguments" })).toContainText('"title": "Retry swallows errors"');
    await expect(call.locator(".tool-part", { hasText: "Progress" })).toContainText("Filing under web");
    await expect(call.locator(".tool-part", { hasText: "Result" })).toContainText("Created OPS-12");
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
