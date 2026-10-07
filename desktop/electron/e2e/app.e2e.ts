// The app as a whole: Electron starts, Work's page is served from `app://`, the preload exposes the
// one bridge object, and the page starts a scripted stand-in for `ling app-server` through it.
// Runs against the Vite build in `.vite/` (`npm run package` first); windows are hidden
// (`MIGHTLING_HIDDEN=1`) so a test does not take the screen.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { _electron as electron, expect, test } from "@playwright/test";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.js");

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");

test("Work opens on app:// and reaches the agent through electronBridge", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-e2e-"));
  // A stand-in `ling`: answers `initialize`, lists no threads, and reports the air-gap level.
  const ling = path.join(home, "ling");
  fs.writeFileSync(ling, `#!/usr/bin/env node
if (process.argv[2] === "airgapped") { console.log("Airgapped: off (default)"); process.exit(0); }
const rl = require("readline").createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const m = JSON.parse(line);
  if (m.method === "initialize") console.log(JSON.stringify({ id: m.id, result: { userAgent: "stand-in" } }));
  else if (m.id !== undefined && m.method) console.log(JSON.stringify({ id: m.id, result: { data: [] } }));
});
`);
  fs.chmodSync(ling, 0o755);
  const app = await electron.launch({
    args: [main, "--work"],
    env: { ...process.env, HOME: home, CODEX_HOME: path.join(home, ".mightling"), MIGHTLING_BIN: ling, MIGHTLING_HIDDEN: "1" },
  });
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    expect(page.url()).toBe("app://-/index.html");
    const type = await page.evaluate(() => (window as unknown as { mightlingWindowType?: string }).mightlingWindowType);
    expect(type).toBe("electron");
    // The page asks the main process to start the agent and initialises it.
    await expect.poll(async () => page.evaluate(() => document.body.innerText), { timeout: 30_000 }).toContain("Mightling");
    // Only the allow-list gets through: a forbidden method is refused by the bridge, not sent.
    const refused = await page.evaluate(async () => {
      try {
        await (window as unknown as { electronBridge: { sendMessageFromView(m: unknown): Promise<unknown> } }).electronBridge.sendMessageFromView({ type: "work/send", message: { id: 99, method: "feedback/upload", params: {} } });
        return "sent";
      } catch (error) {
        return String(error);
      }
    });
    expect(refused).toContain("does not send feedback/upload");
  } finally {
    await app.close();
    fs.rmSync(home, { recursive: true, force: true });
  }
});
