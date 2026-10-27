// The parity backlog's end-to-end sessions (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): the packaged
// main process and page against the scripted stand-in for `ling app-server` (stand-in.ts), under a
// scratch HOME, hidden. Each item has its own `backlog-<n>.e2e.ts` (`backlog.e2e.ts` is B2's).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, type ElectronApplication, type Page } from "@playwright/test";

import { closeApp, launch } from "./apps";

import { projectFolders, runs, writeStandIn } from "./stand-in";

export const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");

export interface Session {
  app: ElectronApplication;
  page: Page;
  home: string;
  close: () => Promise<void>;
}

export interface OpenOptions {
  /** Writes into the scratch HOME before the app starts. */
  prepare?: (home: string) => void;
  /** More environment for the app (and so for the stand-in it starts). */
  env?: Record<string, string>;
  /** More command-line switches for Electron. */
  args?: string[];
  /** Waits for the stand-in's threads in the sidebar (true unless the stand-in lists none). */
  waitForThreads?: boolean;
}

export async function open(options: OpenOptions = {}): Promise<Session> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-backlog-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  options.prepare?.(home);
  const ling = writeStandIn(home, askRoot);
  const app = await launch({
    args: [main, ...(options.args ?? [])],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_ADMIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", MIGHTLING_HIDDEN: "1", ...options.env },
  });
  const page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await page.emulateMedia({ reducedMotion: "reduce" });
  if (options.waitForThreads !== false) await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
  return {
    app, page, home,
    close: async () => {
      await closeApp(app);
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

/** How many `ling app-server` processes the app started: always one. */
export const appServers = (home: string) => runs(home).filter((args) => args.includes("app-server")).length;

/** The window's preferences file under the scratch HOME. */
export const prefsFile = (home: string) => path.join(home, ".local", "share", "dev.dreamference.mightling", "prefs.json");

/** The window's preferences as written so far. */
export const readPrefs = (home: string): Record<string, unknown> => JSON.parse(fs.readFileSync(prefsFile(home), "utf8"));

/** Starts a new Work thread in the fetcher project with `text` as its first message. */
export async function startWork(page: Page, home: string, text: string): Promise<void> {
  await page.getByRole("button", { name: "Work", exact: true }).click();
  await page.getByRole("textbox", { name: "Project folder" }).fill(projectFolders(home).fetcher);
  await page.locator(".composer textarea").fill(text);
  await page.keyboard.press("Enter");
  await expect(page.locator(".composer textarea")).toHaveValue("");
}
