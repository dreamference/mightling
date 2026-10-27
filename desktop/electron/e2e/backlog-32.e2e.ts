// The parity backlog's B32 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): several
// repositories in the review pane, and the pull-request tab's comments, reviews and Create pull request.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-32
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";
import { projectFolders, received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

/** git in a scratch folder, with a made-up identity and no configuration of this machine's. */
const gitIn = (home: string, cwd: string, ...args: string[]) =>
  execFileSync("git", ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "init.defaultBranch=main", ...args], { cwd, env: { ...process.env, HOME: home, GIT_CONFIG_NOSYSTEM: "1" }, encoding: "utf8" });

/** A repository in `folder` with `file` committed, then `edit` appended to it. */
function repo(home: string, folder: string, file: string, text: string, edit: string): void {
  fs.mkdirSync(path.dirname(path.join(folder, file)), { recursive: true });
  fs.writeFileSync(path.join(folder, file), text);
  gitIn(home, folder, "init", "-q");
  gitIn(home, folder, "add", ".");
  gitIn(home, folder, "commit", "-q", "-m", "first");
  if (edit) fs.appendFileSync(path.join(folder, file), edit);
}

test("B32: each repository's lines changed in the selector, and Last turn over All repos or one", async () => {
  const { page, close } = await open({
    prepare: (home) => {
      // The flaky-retry thread's folder holds two repositories; its last turn changed fetcher/client.py.
      const { fetcher } = projectFolders(home);
      repo(home, path.join(fetcher, "fetcher"), "client.py", "def fetch(url):\n    pass\n", "# one\n# two\n");
      repo(home, path.join(fetcher, "docs"), "README.md", "Fetcher\n", "");
    },
  });
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    await page.keyboard.press("Control+Shift+G");
    const pane = page.getByRole("complementary", { name: "Review" });
    const selector = pane.getByRole("combobox", { name: "Repository" });
    await expect(selector.locator("option")).toHaveText(["docs +0 −0", "fetcher +2 −0"]);
    // Last turn: All repos first, each repository with what the turn changed in it.
    await pane.getByRole("navigation", { name: "Scope" }).getByRole("button", { name: "Last turn" }).click();
    await expect(selector.locator("option")).toHaveText([/^All repos \+\d+ −\d+$/, "docs +0 −0", /^fetcher \+\d+ −\d+$/]);
    await expect(pane.locator(".file-name")).toHaveText(["fetcher/client.py"]);
    await selector.selectOption({ index: 1 });
    await expect(pane.locator(".file-name")).toHaveCount(0);
    await expect(pane).toContainText("The last turn changed nothing in docs.");
    await selector.selectOption({ index: 0 });
    await expect(pane.locator(".file-name")).toHaveText(["fetcher/client.py"]);
  } finally {
    await close();
  }
});

/** A stand-in `gh` that records its arguments and answers the tab's questions. */
function fakeGh(home: string): string {
  const gh = path.join(home, "gh");
  const log = path.join(home, "gh.jsonl");
  const pr = { number: 21, title: "Shrink the hero image", author: { login: "ada" }, headRefName: "hero", baseRefName: "main", isDraft: false, reviewDecision: "REVIEW_REQUIRED", updatedAt: "2026-10-25T09:00:00Z", url: "https://github.example/acme/website/pull/21" };
  const activity = {
    comments: [{ author: { login: "brin" }, body: "Does **WebP** load on old phones?", createdAt: "2026-10-25T10:00:00Z" }],
    reviews: [{ author: { login: "cy" }, body: "Keep the JPEG as a fallback.", state: "CHANGES_REQUESTED", submittedAt: "2026-10-25T11:00:00Z" }],
  };
  fs.writeFileSync(gh, `#!/usr/bin/env node
const fs = require("fs");
const args = process.argv.slice(2);
fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify({ args, cwd: process.cwd() }) + "\\n");
const [what, verb] = args;
if (what === "pr" && verb === "list") { console.log(${JSON.stringify(JSON.stringify([pr]))}); process.exit(0); }
if (what === "pr" && verb === "view" && args.includes("comments,reviews")) { console.log(${JSON.stringify(JSON.stringify(activity))}); process.exit(0); }
if (what === "pr" && verb === "view") { console.log(${JSON.stringify(JSON.stringify({ ...pr, body: "Hero at 120 KB.", state: "OPEN", statusCheckRollup: [] }))}); process.exit(0); }
if (what === "pr" && verb === "diff") { process.stdout.write("diff --git a/hero.txt b/hero.txt\\n--- a/hero.txt\\n+++ b/hero.txt\\n@@ -1 +1 @@\\n-big\\n+small\\n"); process.exit(0); }
if (what === "pr" && verb === "create") { console.log("https://github.example/acme/website/pull/22"); process.exit(0); }
process.stderr.write("unknown command\\n"); process.exit(1);
`);
  fs.chmodSync(gh, 0o755);
  return gh;
}

test("B32: the pull request's comments and reviews; Create pull request with a written description; refused at airgapped on", async () => {
  let home = "";
  const session = await open({
    prepare: (dir) => {
      home = dir;
      process.env.MIGHTLING_GH = fakeGh(dir);
      // The hero-image thread's folder is a repository on a branch one commit past main.
      const { website } = projectFolders(dir);
      repo(dir, website, "hero.txt", "big\n", "");
      gitIn(dir, website, "checkout", "-q", "-b", "hero");
      fs.writeFileSync(path.join(website, "hero.txt"), "small\n");
      gitIn(dir, website, "commit", "-q", "-am", "Shrink the hero image to 120 KB");
    },
  });
  const { page, close } = session;
  const calls = (): { args: string[]; cwd: string }[] => {
    try {
      return fs.readFileSync(path.join(home, "gh.jsonl"), "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
    } catch {
      return [];
    }
  };
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Git" }).click();
    await settings.getByRole("checkbox", { name: /A pull-request tab/ }).check();
    const instruction = settings.getByRole("textbox", { name: "Pull request description instruction" });
    await instruction.fill("Start with the user-visible change, then a Testing section.");
    await instruction.blur();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Pull requests" }).click();
    const tab = page.getByRole("tabpanel", { name: "Pull requests" });

    // Comments and reviews beside the changes, in the order they were made.
    await tab.getByRole("list", { name: "Pull requests" }).getByRole("button", { name: /Shrink the hero image/ }).click();
    const conversation = tab.getByRole("region", { name: "Pull request #21" }).getByRole("list", { name: "Comments and reviews" });
    await expect(conversation.locator("li")).toHaveCount(2);
    await expect(conversation.locator("li").first()).toContainText("brin commented");
    await expect(conversation.locator("li").first().locator("strong", { hasText: "WebP" })).toBeVisible();
    await expect(conversation.locator("li").nth(1)).toContainText("cy requested changes");
    expect(calls().some((call) => call.args.join(" ") === "pr view 21 --json comments,reviews")).toBe(true);

    // Create pull request: the title from the branch's last commit, the description from the local model.
    await tab.getByRole("button", { name: "Create pull request" }).click();
    const form = tab.getByRole("region", { name: "Create a pull request" });
    await expect(form.getByRole("textbox", { name: "Title" })).toHaveValue("Shrink the hero image to 120 KB");
    await form.getByRole("button", { name: "Write the description" }).click();
    await expect(form.getByRole("textbox", { name: "Description" })).toHaveValue(/^Re-raise the last error in the retry loop/);
    const asked = received(home).filter((m) => m.method === "turn/start").map((m) => JSON.stringify(m.params));
    expect(asked.some((text) => text.includes("Start with the user-visible change, then a Testing section.") && text.includes("Shrink the hero image to 120 KB") && text.includes("+small"))).toBe(true);
    await form.getByRole("textbox", { name: "Base branch" }).fill("main");
    page.once("dialog", (dialog) => void dialog.accept());
    await form.getByRole("button", { name: "Create", exact: true }).click();
    await expect.poll(() => calls().find((call) => call.args[1] === "create")?.args).toEqual(
      ["pr", "create", "--title=Shrink the hero image to 120 KB", expect.stringMatching(/^--body=Re-raise the last error/), "--base=main"]);
    // The inbox is read again after the pull request is created.
    await expect.poll(() => calls().filter((call) => call.args[1] === "list").length).toBe(2);
    expect(calls().every((call) => call.cwd === projectFolders(home).website)).toBe(true);

    // Airgapped on: the main process refuses both before gh runs.
    fs.writeFileSync(path.join(home, "airgapped-on"), "");
    const before = calls().length;
    const ask = (message: object) => page.evaluate((sent) => (window as unknown as { electronBridge: { sendMessageFromView(message: object): Promise<unknown> } }).electronBridge
      .sendMessageFromView(sent).then(() => "ran", (error: unknown) => String(error)), message);
    const website = projectFolders(home).website;
    expect(await ask({ type: "pr/create", root: website, draft: { title: "x", body: "", base: null, draft: false }, thread: "w3" })).toContain("airgapped is on");
    expect(await ask({ type: "pr/activity", root: website, number: 21, thread: "w3" })).toContain("airgapped is on");
    expect(calls()).toHaveLength(before);
  } finally {
    delete process.env.MIGHTLING_GH;
    await close();
  }
});
