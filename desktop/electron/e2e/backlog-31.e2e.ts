// The parity backlog's B31 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): office files previewed.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-31
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { projectFolders } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

/** A one-page PDF (a blue square), as a converter would write it, in base64. */
const PDF = "JVBERi0xLjQKMSAwIG9iajw8L1R5cGUvQ2F0YWxvZy9QYWdlcyAyIDAgUj4+ZW5kb2JqCjIgMCBvYmo8PC9UeXBlL1BhZ2VzL0tpZHNbMyAwIFJdL0NvdW50IDE+PmVuZG9iagozIDAgb2JqPDwvVHlwZS9QYWdlL1BhcmVudCAyIDAgUi9NZWRpYUJveFswIDAgMzAwIDIwMF0vQ29udGVudHMgNCAwIFIvUmVzb3VyY2VzPDw+Pj4+ZW5kb2JqCjQgMCBvYmo8PC9MZW5ndGggMjc+PnN0cmVhbQowIDAgMSByZyAyMCAyMCAyNjAgMTYwIHJlIGYKZW5kc3RyZWFtIGVuZG9iagp4cmVmCjAgNQowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMDkgMDAwMDAgbiAKMDAwMDAwMDA1MiAwMDAwMCBuIAowMDAwMDAwMTAxIDAwMDAwIG4gCjAwMDAwMDAxOTMgMDAwMDAgbiAKdHJhaWxlcjw8L1NpemUgNS9Sb290IDEgMCBSPj4Kc3RhcnR4cmVmCjI2NgolJUVPRgo=";

test("B31: an office file's tab shows it as pages, converted by LibreOffice with no network, or says why it cannot", async () => {
  let soffice = "";
  const { page, close } = await open({
    prepare: (home) => {
      const { website } = projectFolders(home);
      fs.writeFileSync(path.join(website, "plan.docx"), "not really a document");
      fs.writeFileSync(path.join(website, "old.odt"), "nor this");
      // A stand-in LibreOffice: writes <outdir>/<name>.pdf, or fails for old.odt.
      soffice = path.join(home, "soffice");
      fs.writeFileSync(soffice, `#!/bin/bash
out=""; prev=""; for a in "$@"; do [ "$prev" = "--outdir" ] && out="$a"; prev="$a"; last="$a"; done
name=$(basename "$last"); [ "$name" = old.odt ] && exit 3
echo ${PDF} | base64 -d > "$out/\${name%.*}.pdf"
`);
      fs.chmodSync(soffice, 0o755);
      process.env.MIGHTLING_SOFFICE = soffice;
    },
  });
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+Shift+E");
    const tree = page.getByRole("navigation", { name: "Files" });
    await tree.getByRole("button", { name: "plan.docx" }).click();
    const tab = page.getByRole("tabpanel", { name: "plan.docx" });
    await expect(tab).toContainText("converted by LibreOffice, with no network");
    await expect(tab.locator("canvas.pdf-page")).toHaveCount(1, { timeout: 30_000 });
    await tree.getByRole("button", { name: "old.odt" }).click();
    await expect(page.getByRole("tabpanel", { name: "old.odt" }).getByRole("status")).toContainText("could not convert this file (exit code 3)");
  } finally {
    delete process.env.MIGHTLING_SOFFICE;
    await close();
  }
});
