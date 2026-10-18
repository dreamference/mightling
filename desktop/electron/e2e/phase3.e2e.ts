// Phase 3 of DESKTOP_PARITY, end to end: the packaged main process and page against the scripted
// stand-in for `ling app-server` (stand-in.ts), one test per item, each checking what its §2 rows
// ship and that the app still runs one app-server. With MIGHTLING_LOOK_DIR set it saves light
// pictures of each item there, the window shown; without it the window is hidden. Needs a display
// and the Vite build (`npm run package` first).
//   MIGHTLING_LOOK_DIR=~/.local/share/dreamference/desktop-phase3 npm run e2e -- phase3
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { _electron as electron, expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { projectFolders, received, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");

interface Session {
  app: ElectronApplication;
  page: Page;
  home: string;
  shoot: (name: string) => Promise<void>;
  close: () => Promise<void>;
}

async function open(prepare?: (home: string) => void): Promise<Session> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-p3-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  prepare?.(home);
  const ling = writeStandIn(home, askRoot);
  const app = await electron.launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", ...(out ? {} : { MIGHTLING_HIDDEN: "1" }) },
  });
  const page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
  const shoot = async (name: string) => {
    if (!out) return;
    fs.mkdirSync(out, { recursive: true });
    await page.mouse.move(40, 840);
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(out, name) });
  };
  return {
    app, page, home, shoot,
    close: async () => {
      await app.close();
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

const appServers = (home: string) => runs(home).filter((args) => args.includes("app-server")).length;

/** A small project in the `website` folder: a page, its script, a stylesheet and a readme. */
function website(home: string): string {
  const root = projectFolders(home).website;
  fs.mkdirSync(path.join(root, "src"), { recursive: true });
  fs.mkdirSync(path.join(root, ".git"), { recursive: true });
  fs.writeFileSync(path.join(root, "README.md"), "# Website\n\nThe hero image is compressed by `npm run images`.\n");
  fs.writeFileSync(path.join(root, "src", "hero.ts"), [
    "// The hero image, served at the width the screen asks for.",
    "export function heroSource(width: number): string {",
    "  const sizes = [640, 1280, 1920];",
    "  const fit = sizes.find((size) => size >= width) ?? sizes[sizes.length - 1];",
    "  return `/images/hero-${fit}.webp`;",
    "}",
    "",
    ...Array.from({ length: 60 }, (_, index) => `export const step${index + 1} = ${index + 1} * 16; // spacing ${index + 1}`),
    "",
  ].join("\n"));
  fs.writeFileSync(path.join(root, "src", "site.css"), ".hero { width: 100%; aspect-ratio: 16 / 9; }\n");
  return root;
}

test("1 file tree and file tabs: the folder read and followed, files highlighted, tabs beside Review", async () => {
  const { page, home, shoot, close } = await open(website);
  const root = projectFolders(home).website;
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+Shift+E");
    const tree = page.getByRole("navigation", { name: "Files" });
    await expect(tree).toBeVisible();
    // Folders first, .git left out.
    await expect(tree.locator(".tree-row")).toHaveText(["src", "README.md"]);
    await tree.getByRole("button", { name: "src" }).click();
    await expect(tree.locator(".tree-row")).toHaveText(["src", "hero.ts", "site.css", "README.md"]);
    await tree.getByRole("button", { name: "hero.ts" }).click();
    const tab = page.getByRole("tabpanel", { name: "hero.ts" });
    await expect(tab.locator(".code-text")).toContainText("export function heroSource");
    await expect(tab.locator(".hljs-keyword").first()).toHaveText("export");
    await expect(tab.locator(".file-meta")).toContainText("67 lines");
    // A file written by the agent appears in the open folder; an edit to the open file shows.
    fs.writeFileSync(path.join(root, "src", "hero.test.ts"), "test('fits', () => {});\n");
    await expect(tree.getByRole("button", { name: "hero.test.ts" })).toBeVisible();
    fs.appendFileSync(path.join(root, "src", "hero.ts"), "export const edited = true;\n");
    await expect(tab.locator(".code-text")).toContainText("export const edited = true;");
    await shoot("1-files-tree-tab.png");

    // A second file, then the Review tab beside them; Ctrl+W closes the tab in front, Ctrl+Shift+T reopens it.
    await tree.getByRole("button", { name: "README.md" }).click();
    await expect(page.getByRole("tab")).toHaveText([/hero\.ts/, /README\.md/]);
    await page.keyboard.press("Control+Shift+G");
    await expect(page.getByRole("tab")).toHaveText([/hero\.ts/, /README\.md/, /Review/]);
    await expect(page.getByRole("complementary", { name: "Review" })).toBeVisible();
    await page.keyboard.press("Control+w");
    await expect(page.getByRole("tab")).toHaveText([/hero\.ts/, /README\.md/]);
    await page.keyboard.press("Control+Shift+T");
    await expect(page.getByRole("tab")).toHaveText([/hero\.ts/, /README\.md/, /Review/]);
    await page.getByRole("tab", { name: /hero\.ts/ }).click();
    await expect(page.getByRole("tabpanel", { name: "hero.ts" })).toBeVisible();
    // Mention puts the file in the composer.
    await page.getByRole("tabpanel", { name: "hero.ts" }).getByRole("button", { name: "Mention" }).click();
    await expect(page.locator(".composer textarea")).toHaveValue(new RegExp(`@${root}/src/hero\\.ts`));
    await page.getByRole("button", { name: "Close the README.md tab" }).click();
    await expect(page.getByRole("tab")).toHaveText([/hero\.ts/, /Review/]);
    await shoot("1-files-with-review.png");

    // The folder was read and followed through the server; nothing was written through it.
    const methods = received(home).map((message) => message.method);
    expect(methods).toContain("fs/readDirectory");
    expect(methods).toContain("fs/watch");
    expect(methods).toContain("fs/readFile");
    expect(methods.filter((method) => method === "fs/writeFile" || method === "fs/remove")).toEqual([]);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});

test("2 search files and go to line: Ctrl+P by name, name:line, and Ctrl+L in a file tab", async () => {
  const { page, home, shoot, close } = await open(website);
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+p");
    const search = page.getByRole("dialog", { name: "Search files" });
    await search.getByRole("textbox", { name: "File name" }).fill("site");
    await expect(search.getByRole("option")).toHaveCount(1);
    await expect(search.getByRole("option")).toContainText("src/site.css");
    await shoot("2-search-files.png");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("tabpanel", { name: "site.css" })).toContainText(".hero");
    // name:line opens the file at that line, marked.
    await page.keyboard.press("Control+p");
    await search.getByRole("textbox", { name: "File name" }).fill("hero.ts:40");
    await expect(search.getByRole("option").first()).toContainText("hero.ts");
    await page.keyboard.press("Enter");
    const hero = page.getByRole("tabpanel", { name: "hero.ts" });
    await expect(hero.locator(".code-gutter .marked")).toHaveText("40");
    // Ctrl+L in the file tab: go to line.
    await hero.locator(".code-view").click();
    await page.keyboard.press("Control+l");
    await expect(search.getByRole("textbox", { name: "File name" })).toHaveValue(":");
    await page.keyboard.type("5");
    await expect(search.getByRole("option")).toHaveText("Go to line 5");
    await page.keyboard.press("Enter");
    await expect(hero.locator(".code-gutter .marked")).toHaveText("5");
    await shoot("2-go-to-line.png");
    expect(received(home).filter((message) => message.method === "fuzzyFileSearch").length).toBeGreaterThan(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
