// A project's page in Ask and its knowledge searched through ling-docs (specs/DREAMFERENCE_MIGHTLING_
// DESKTOP_PARITY_CLAUDE.md §4.7) end to end: the packaged main process and page against the scripted
// stand-in for `ling` (stand-in.ts), whose `docs` keeps its collections in a file. The file dialog is
// answered in the main process. Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- knowledge
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";
import { projectFolders, received, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("a project's page in Ask: instructions, knowledge made searchable, its questions; the context tells the agent to search it", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-knowledge-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  const checklist = path.join(home, "checklist.md");
  fs.writeFileSync(checklist, "# Release checklist\n\nSign the manifest before publishing.\n");
  const ling = writeStandIn(home, askRoot);
  const app = await launch({ args: [main], env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", ...(out ? {} : { MIGHTLING_HIDDEN: "1" }) } });
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
    // The file dialog, answered with the checklist.
    await app.evaluate(({ dialog }, file) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [file] })) as typeof dialog.showOpenDialog;
    }, checklist);

    // A project made in Ask, with instructions.
    await page.getByRole("button", { name: "New project" }).click();
    const made = page.getByRole("dialog", { name: "New project" });
    await made.getByRole("textbox", { name: "Project name" }).fill("Adapters");
    await made.getByRole("textbox", { name: "Project instructions" }).fill("Cite the spec section for every claim.");
    await made.getByRole("button", { name: "Create project" }).click();
    await expect(made).toHaveCount(0);
    await page.locator(".server-project h2", { hasText: "Adapters" }).click();

    // Its page: the instructions, no knowledge yet; a file added becomes a collection the agent searches.
    const project = page.locator(".project-page");
    await expect(project.getByRole("heading", { name: "Adapters" })).toBeVisible();
    await expect(project).toContainText("Cite the spec section for every claim.");
    await expect(project).toContainText("No files yet.");
    await project.getByRole("button", { name: "Add files…" }).click();
    await expect(project.locator(".project-files")).toContainText("checklist.md");
    await expect(project.locator(".knowledge-reach")).toContainText("searches these files", { timeout: 10_000 });
    await expect.poll(() => runs(home).filter((args) => args[0] === "docs").map((args) => args[1])).toEqual(expect.arrayContaining(["add", "reindex"]));
    const added = runs(home).find((args) => args[0] === "docs" && args[1] === "add") as string[];
    expect(added[2]).toMatch(/[\\/]projects[\\/]proj-1[\\/]knowledge$/);
    expect(added.slice(3)).toEqual(["--name", "project-proj-1"]);
    if (out) await page.screenshot({ path: path.join(out, "project-page.png") });

    // A question in the project: its first turn carries the context, with the collection to search.
    await page.locator(".composer textarea").fill("What must happen before publishing?");
    await page.keyboard.press("Enter");
    await expect(page.locator(".item.agent").last()).toContainText("Done", { timeout: 15_000 });
    const first = received(home).filter((line) => line.method === "turn/start").pop();
    expect(JSON.stringify(first?.params)).toContain('docs_search with collection \\"project-proj-1\\"');
    // The question is listed on the project's page.
    await page.locator(".server-project h2", { hasText: "Adapters" }).click();
    await expect(page.locator(".project-page .project-questions")).toContainText("What must happen before publishing?");
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});
