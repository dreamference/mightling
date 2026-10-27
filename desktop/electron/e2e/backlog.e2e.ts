// The parity backlog (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16), end to end: the packaged main
// process and page against the scripted stand-in for `ling app-server` (stand-in.ts), one test per
// item, each checking what it ships and that the app still runs one app-server. Needs a display and
// the Vite build (`npm run package` first).
//   npm run e2e -- backlog
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";

import { projectFolders, received, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

interface Session {
  app: ElectronApplication;
  page: Page;
  home: string;
  close: () => Promise<void>;
}

async function open(prepare?: (home: string) => void): Promise<Session> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-backlog-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  prepare?.(home);
  const ling = writeStandIn(home, askRoot);
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_ADMIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", MIGHTLING_HIDDEN: "1" },
  });
  const page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
  return {
    app, page, home,
    close: async () => {
      await closeApp(app);
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

const appServers = (home: string) => runs(home).filter((args) => args.includes("app-server")).length;

/** Starts a new Work thread in the fetcher project with `text` as its first message. */
async function startWork(page: Page, home: string, text: string): Promise<void> {
  await page.getByRole("button", { name: "Work", exact: true }).click();
  await page.getByRole("textbox", { name: "Project folder" }).fill(projectFolders(home).fetcher);
  await page.locator(".composer textarea").fill(text);
  await page.keyboard.press("Enter");
}

test("B2: a question answered in full: descriptions shown, None of the above in the user's words, a secret hidden", async () => {
  const { page, home, close } = await open();
  try {
    await startWork(page, home, "Tidy the client with a question first.");
    const card = page.locator(".approval.questions");
    await expect(card).toContainText("Which part first?");
    await expect(card).toContainText("fetcher/client.py and its retry loop");
    const answer = card.getByRole("button", { name: "Answer" });
    await expect(answer).toBeDisabled();
    // "Other": a field for the user's words appears, and the answer waits for them.
    await card.getByRole("radio", { name: /None of the above/ }).check();
    await expect(answer).toBeDisabled();
    await card.getByRole("textbox", { name: "Your answer to Scope" }).fill("The logging first");
    // The secret is typed into a hidden field.
    const secret = card.getByLabel("Token", { exact: true });
    await expect(secret).toHaveAttribute("type", "password");
    await secret.fill("s3cret");
    await answer.click();
    await expect.poll(() => received(home).find((m) => m.id === "input-1")?.result).toEqual({
      answers: { scope: { answers: ["None of the above", "user_note: The logging first"] }, token: { answers: ["user_note: s3cret"] } },
    });
    await expect(card).toHaveCount(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
