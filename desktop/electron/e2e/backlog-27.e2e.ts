// The parity backlog's B27 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): CSV as a table, HTML to the preview.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-27
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { projectFolders } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B27: a CSV tab is a table, and an HTML file opens in the preview tab from its own tab", async () => {
  const { page, close } = await open({
    prepare: (home) => {
      const { website } = projectFolders(home);
      fs.writeFileSync(path.join(website, "sales.csv"), 'region,"total, EUR"\nNorth,1200\n"South ""B""",950\n');
      fs.writeFileSync(path.join(website, "page.html"), "<!doctype html><h1>The hero</h1>");
    },
  });
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+Shift+E");
    const tree = page.getByRole("navigation", { name: "Files" });
    await tree.getByRole("button", { name: "sales.csv" }).click();
    const table = page.getByRole("tabpanel", { name: "sales.csv" }).getByRole("table");
    await expect(table.getByRole("columnheader")).toHaveText(["region", "total, EUR"]);
    await expect(table.locator("tbody tr").nth(1)).toHaveText(['South "B"950']);
    await tree.getByRole("button", { name: "page.html" }).click();
    await page.getByRole("tabpanel", { name: "page.html" }).getByRole("button", { name: "Open in the preview tab" }).click();
    await expect(page.getByRole("textbox", { name: "Address" })).toHaveValue(/page\.html$/);
  } finally {
    await close();
  }
});
