// Phase 3 of DESKTOP_PARITY, end to end: the packaged main process and page against the scripted
// stand-in for `ling app-server` (stand-in.ts), one test per item, each checking what its §2 rows
// ship and that the app still runs one app-server. With MIGHTLING_LOOK_DIR set it saves light
// pictures of each item there, the window shown; without it the window is hidden. Needs a display
// and the Vite build (`npm run package` first).
//   MIGHTLING_LOOK_DIR=~/.local/share/dreamference/desktop-phase3 npm run e2e -- phase3
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
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
    const file = path.join(out, name);
    await page.screenshot({ path: file });
    // A preview tab's page is a view of its own, which the page's screenshot does not hold: drawn in over it.
    const views = await app.evaluate(async ({ BrowserWindow }) => {
      const found: { x: number; y: number; png: string }[] = [];
      for (const window of BrowserWindow.getAllWindows()) {
        for (const child of window.contentView.children) {
          const view = child as unknown as { getVisible(): boolean; getBounds(): { x: number; y: number }; webContents?: { capturePage(): Promise<{ toPNG(): Buffer }> } };
          if (!view.webContents || !view.getVisible()) continue;
          const image = await view.webContents.capturePage();
          found.push({ ...view.getBounds(), png: image.toPNG().toString("base64") });
        }
      }
      return found;
    });
    // The screenshot is in device pixels, the views' bounds in the window's (DIP).
    const scale = await page.evaluate(() => devicePixelRatio);
    views.forEach((view, index) => {
      const part = `${file}.view${index}.png`;
      fs.writeFileSync(part, Buffer.from(view.png, "base64"));
      execFileSync("python3", ["-c", "import sys\nfrom PIL import Image\nbase=Image.open(sys.argv[1]); part=Image.open(sys.argv[2]); base.paste(part,(int(sys.argv[3]),int(sys.argv[4]))); base.save(sys.argv[1])", file, part, String(Math.round(view.x * scale)), String(Math.round(view.y * scale))]);
      fs.rmSync(part);
    });
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

const PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const PDF = [
  "%PDF-1.4",
  "1 0 obj <</Type/Catalog/Pages 2 0 R>> endobj",
  "2 0 obj <</Type/Pages/Kids[3 0 R]/Count 1>> endobj",
  "3 0 obj <</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>> endobj",
  "4 0 obj <</Length 44>> stream",
  "BT /F1 24 Tf 40 100 Td (Hello PDF) Tj ET",
  "endstream endobj",
  "5 0 obj <</Type/Font/Subtype/Type1/BaseFont/Helvetica>> endobj",
  "trailer <</Root 1 0 R>>",
  "%%EOF",
  "",
].join("\n");

/** The website project with documents: a readme with a picture and a link, a page with a script, a PDF. */
function documents(home: string): void {
  const root = website(home);
  fs.mkdirSync(path.join(root, "docs", "img"), { recursive: true });
  fs.writeFileSync(path.join(root, "docs", "img", "pixel.png"), Buffer.from(PIXEL, "base64"));
  fs.writeFileSync(path.join(root, "docs", "guide.md"), [
    "# The image guide", "", "Every hero image is **WebP**, at three widths.", "",
    "![one pixel](img/pixel.png)", "", "| Width | Use |", "|---|---|", "| 640 | phones |", "| 1920 | desktops |", "",
    "See the [stylesheet](../src/site.css) and [the vendor's page](https://example.com/images).", "",
  ].join("\n"));
  fs.writeFileSync(path.join(root, "docs", "page.html"), [
    "<!doctype html><html><head><title>Hero</title><style>h1 { color: rgb(10, 186, 181); }</style></head><body>",
    "<h1>The hero page</h1><p>Compressed images load first.</p>",
    "<img src='img/pixel.png' alt='pixel' onerror='window.ran = 1'>",
    "<script>window.ran = 1; document.title = 'ran';</script>",
    "<form action='https://example.com'><input name='q'></form>",
    "<a href='guide.md'>The guide</a> <a href='javascript:window.ran=1'>a trap</a>",
    "</body></html>",
  ].join("\n"));
  fs.writeFileSync(path.join(root, "docs", "report.pdf"), PDF);
}

test("3 artifacts viewer: Markdown with its picture, HTML without scripts, a PDF, an image, Source", async () => {
  const { page, home, shoot, close } = await open(documents);
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+Shift+E");
    const tree = page.getByRole("navigation", { name: "Files" });
    await tree.getByRole("button", { name: "docs" }).click();
    await tree.getByRole("button", { name: "guide.md" }).click();
    const guide = page.getByRole("tabpanel", { name: "guide.md" });
    await expect(guide.getByRole("heading", { name: "The image guide" })).toBeVisible();
    await expect(guide.locator("img")).toHaveAttribute("src", /^blob:/);
    await expect(guide.locator("table")).toContainText("desktops");
    await shoot("3-artifact-markdown.png");
    // Source shows the text; a local link opens its file, a remote one goes nowhere.
    await guide.getByRole("radio", { name: "Source" }).click();
    await expect(guide.locator(".code-text")).toContainText("![one pixel](img/pixel.png)");
    await guide.getByRole("radio", { name: "Rendered" }).click();
    await guide.locator("[data-href='https://example.com/images']").click();
    await guide.locator("[data-href='../src/site.css']").click();
    await expect(page.getByRole("tabpanel", { name: "site.css" })).toContainText(".hero");

    await tree.getByRole("button", { name: "page.html" }).click();
    const html = page.getByRole("tabpanel", { name: "page.html" });
    await expect(html.getByRole("heading", { name: "The hero page" })).toHaveCSS("color", "rgb(10, 186, 181)");
    await expect(html.locator("img")).toHaveAttribute("src", /^blob:/);
    await expect(html.locator("form, input, script")).toHaveCount(0);
    await expect(html.locator(".artifact-note")).toContainText("left out");
    await html.getByText("a trap").click();
    await html.getByText("The guide").click();
    expect(await page.evaluate(() => (window as unknown as { ran?: number }).ran)).toBeUndefined();
    expect(await page.title()).not.toBe("ran");
    await page.getByRole("tab", { name: /page\.html/ }).click();
    await shoot("3-artifact-html.png");

    await tree.getByRole("button", { name: "report.pdf" }).click();
    const pdf = page.getByRole("tabpanel", { name: "report.pdf" });
    // pdf.js and its worker load on the first PDF; a cold start takes a moment.
    await expect(pdf.locator(".pdf-tools")).toContainText("1 page", { timeout: 20_000 });
    await expect(pdf.locator("canvas[aria-label='Page 1']")).toBeVisible({ timeout: 20_000 });
    await shoot("3-artifact-pdf.png");

    await tree.getByRole("button", { name: "img" }).click();
    await tree.getByRole("button", { name: "pixel.png" }).click();
    const image = page.getByRole("tabpanel", { name: "pixel.png" });
    await expect(image.locator(".image-size")).toHaveText("1 × 1");
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});

/** A dev server on this machine: a page with a hero, a picture from the internet, a link out. */
async function devServer(): Promise<{ url: string; hits: string[]; stop: () => Promise<void> }> {
  const hits: string[] = [];
  const server = http.createServer((request, response) => {
    hits.push(request.url ?? "");
    response.setHeader("content-type", "text/html");
    response.end(`<!doctype html><title>Hero preview</title><body style="font:16px sans-serif;margin:24px">
      <h1 id="hero" style="color:#0abab5">The hero, compressed</h1><p class="lede">Loads in 180 ms on a phone.</p>
      <img src="https://example.com/remote.png" alt="remote"><a id="out" href="https://example.com/">Read more</a></body>`);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  return { url: `127.0.0.1:${port}/`, hits, stop: () => new Promise((resolve) => server.close(() => resolve())) };
}

test("4 preview tab: a local page, comment mode to the composer, a site out on the internet asked and blocked", async () => {
  const site = await devServer();
  const { app, page, home, shoot, close } = await open(website);
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+t");
    const address = page.getByRole("textbox", { name: "Address" });
    await expect(address).toBeFocused();
    await address.fill(site.url);
    await address.press("Enter");
    await expect(page.getByRole("tab", { name: /Hero preview/ })).toBeVisible({ timeout: 20_000 });
    expect(site.hits).toContain("/");
    // The page is a view of its own, not the app's page.
    const previewed = await expect.poll(() => app.windows().find((entry) => entry.url().startsWith("http://127.0.0.1"))?.url() ?? "", { timeout: 10_000 }).toContain("127.0.0.1");
    void previewed;
    const view = app.windows().find((entry) => entry.url().startsWith("http://127.0.0.1"))!;
    expect(await view.evaluate(() => typeof (window as unknown as { electronBridge?: unknown }).electronBridge)).toBe("undefined");
    // The picture from the internet asked rather than loaded.
    await expect(page.locator(".preview-ask")).toContainText("example.com");
    await shoot("4-preview-ask.png");
    await page.locator(".preview-ask").getByRole("button", { name: "Block" }).click();

    // Comment mode: an element of the page, a comment, to the composer.
    await page.getByRole("button", { name: "Comment on the page" }).click();
    await expect(page.locator(".preview-note")).toBeVisible();
    await view.locator("#hero").click();
    const box = page.getByRole("textbox", { name: "Comment on the element" });
    await expect(box).toBeVisible();
    await expect(page.locator(".preview-comment .picked")).toContainText("h1#hero");
    await box.fill("Make the heading one line on phones.");
    await shoot("4-preview-comment.png");
    await page.getByRole("button", { name: "Send to the composer" }).click();
    await expect(page.locator(".composer textarea")).toHaveValue(/On the page http:\/\/127\.0\.0\.1:\d+\/, the element `h1#hero` \("The hero, compressed"\): Make the heading one line on phones\./);

    // The link out is blocked now (the user's choice), and Settings › Browser shows the choice.
    await view.locator("#out").click({ noWaitAfter: true });
    await expect(page.locator(".preview-blocked")).toContainText("blocked in Settings");
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Browser" }).click();
    await expect(settings.getByRole("combobox", { name: "example.com: allow or block" })).toHaveValue("block");
    await shoot("4-preview-settings.png");
    await page.keyboard.press("Escape");

    // Bigger text: the view still sits on the tab's body.
    await page.locator(".preview-tools").click({ position: { x: 2, y: 2 } });
    await page.keyboard.press("Control+=");
    await page.waitForTimeout(500);
    // The view sits exactly on the tab's body, in the window's pixels, at 110% text too.
    const surface = await page.locator(".preview-surface").evaluate((element) => {
      const zoom = (element as HTMLElement & { currentCSSZoom?: number }).currentCSSZoom ?? 1;
      const r = element.getBoundingClientRect();
      return { zoom, x: Math.round(r.x * zoom), y: Math.round(r.y * zoom), width: Math.round(r.width * zoom) };
    });
    expect(surface.zoom).toBeCloseTo(1.1);
    await expect.poll(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().flatMap((w) => w.contentView.children.map((c) => (c as unknown as { getBounds(): { x: number; y: number; width: number } }).getBounds()))[0]))
      .toMatchObject({ x: surface.x, y: surface.y, width: surface.width });
    await shoot("4-preview-zoomed.png");
    await page.keyboard.press("Control+0");
    expect(site.hits.some((hit) => hit.includes("remote"))).toBe(false);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
    await site.stop();
  }
});

test("5 activity: what the agent did across threads, by kind and project, a row opening its thread", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.keyboard.press("Control+Alt+u");
    const activity = page.getByRole("dialog", { name: "Activity" });
    const rows = activity.getByRole("listitem");
    await expect(rows.filter({ hasText: "Ran pytest tests/test_fetch.py -q -k retry (exit 1)" })).toHaveCount(1);
    await expect(rows.filter({ hasText: "Edited client.py" })).toHaveCount(1);
    await expect(rows.filter({ hasText: "Ran pytest tests/test_fetch.py" })).toHaveClass(/failed/);
    await shoot("5-activity.png");
    await activity.getByRole("button", { name: "Commands" }).click();
    await expect(rows.filter({ hasText: "Ran " })).toHaveCount(0);
    await expect(rows.filter({ hasText: "Edited client.py" })).toHaveCount(1);
    await rows.filter({ hasText: "Edited client.py" }).click();
    await expect(activity).toHaveCount(0);
    await expect(page.locator(".bar .title")).toHaveText("Fix the flaky retry test");
    expect(received(home).filter((message) => message.method === "thread/turns/list").length).toBeGreaterThan(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});

test("6 pets: Mightling's ling above the windows, working with a turn, a quick question to Ask, Super+Alt+P", async () => {
  const { app, page, home, close } = await open();
  try {
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Pets" }).click();
    await settings.getByRole("checkbox", { name: "Show the pet" }).check();
    await page.keyboard.press("Escape");
    await expect.poll(() => app.windows().some((entry) => entry.url().endsWith("#pet")), { timeout: 15_000 }).toBe(true);
    const pet = app.windows().find((entry) => entry.url().endsWith("#pet"))!;
    const body = pet.locator(".pet-body");
    await expect(body).toHaveAttribute("aria-label", /Resting/);
    // A turn starts: the pet works.
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.locator(".composer textarea").fill("Try WebP at 80% quality.");
    await page.keyboard.press("Enter");
    await expect(body).toHaveClass(/mood-working/);
    await expect(body).toHaveAttribute("aria-label", /One thread working/);
    if (out) {
      fs.mkdirSync(out, { recursive: true });
      await pet.screenshot({ path: path.join(out, "6-pet-working.png"), omitBackground: true });
    }
    // A click: the bubble, and a quick question that lands in Ask's composer.
    await body.click();
    const bubble = pet.getByRole("dialog", { name: "Mightling pet" });
    await expect(bubble).toContainText("One thread working.");
    if (out) await pet.screenshot({ path: path.join(out, "6-pet-bubble.png"), omitBackground: true });
    await bubble.getByRole("textbox", { name: "A quick question" }).fill("How big is the hero image now?");
    await bubble.getByRole("textbox", { name: "A quick question" }).press("Enter");
    await expect(page.locator(".view-ask")).toBeVisible();
    await expect(page.locator(".composer textarea")).toHaveValue("How big is the hero image now?");
    // Super+Alt+P hides it.
    await page.locator(".turns").click({ position: { x: 10, y: 10 } });
    await page.keyboard.press("Meta+Alt+p");
    await expect.poll(() => app.windows().some((entry) => entry.url().endsWith("#pet"))).toBe(false);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});

test("7 suggested prompts: off by default; on, a finished turn brings three follow-ups, a click fills the composer", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(projectFolders(home).fetcher);
    await page.locator(".composer textarea").fill("Make the retry test deterministic.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent").last()).toContainText("Done: the retry counter");
    await page.waitForTimeout(800);
    await expect(page.getByRole("group", { name: "Suggested follow-ups" })).toHaveCount(0);
    expect(received(home).filter((message) => message.method === "thread/start" && (message.params as { ephemeral?: boolean })?.ephemeral)).toHaveLength(0);

    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Suggested prompts" }).click();
    await settings.getByRole("checkbox", { name: "Suggest follow-ups when a turn ends" }).check();
    await page.keyboard.press("Escape");
    await page.locator(".composer textarea").fill("Now log every retry.");
    await page.keyboard.press("Enter");
    const group = page.getByRole("group", { name: "Suggested follow-ups" });
    await expect(group.getByRole("button", { name: "Log the URL on every retry" })).toBeVisible({ timeout: 10_000 });
    await expect(group.locator(".suggestion")).toHaveCount(3);
    await shoot("7-suggested-prompts.png");
    await group.getByRole("button", { name: "Add a test for the retry limit" }).click();
    await expect(page.locator(".composer textarea")).toHaveValue("Add a test for the retry limit");
    await expect(group).toHaveCount(0);
    const unkept = received(home).filter((message) => message.method === "thread/start" && (message.params as { ephemeral?: boolean; sandbox?: string })?.ephemeral);
    expect(unkept).toHaveLength(1);
    expect((unkept[0].params as { sandbox?: string }).sandbox).toBe("read-only");
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
