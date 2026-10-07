// Playwright drives the built Electron app (needs a display: `DISPLAY`, or `xvfb-run` in CI).
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.e2e\.ts/,
  outputDir: "results",
  timeout: 120_000,
  retries: 0,
  workers: 1,
  reporter: "list",
});
