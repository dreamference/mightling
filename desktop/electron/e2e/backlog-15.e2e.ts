// The parity backlog's B15 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): diagrams in answers.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-15
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, startWork } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B15: a mermaid block in an answer is drawn as a diagram, its source a click away", async () => {
  const { page, home, close } = await open();
  try {
    await startWork(page, home, "Explain the retry with a diagram.");
    const figure = page.locator(".item.agent figure.diagram");
    await expect(figure.locator("svg")).toBeVisible();
    await expect(figure.locator("svg")).toContainText("Wait, then retry");
    // Nothing in it can act: no script, no foreign HTML.
    expect(await figure.locator("script, foreignObject").count()).toBe(0);
    await figure.locator(".diagram-source summary").click();
    await expect(figure.locator(".diagram-source code")).toContainText("flowchart LR");
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
