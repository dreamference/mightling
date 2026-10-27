// Phase 2 of DESKTOP_PARITY, end to end: the packaged main process and page against a scripted
// stand-in for `ling app-server` (stand-in.ts), one test per item, each checking what its §2 rows
// ship and that the app still runs one app-server. With MIGHTLING_LOOK_DIR set it also saves light
// pictures of each item there (the owner's screenshots), the window shown; without it the window is
// hidden. Needs a display and the Vite build (`npm run package` first).
//   MIGHTLING_LOOK_DIR=/tmp/p2 npm run e2e -- phase2
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type ElectronApplication, type Page } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";

import { projectFolders, received, runs, writeStandIn } from "./stand-in";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

interface Session {
  app: ElectronApplication;
  page: Page;
  home: string;
  shoot: (name: string) => Promise<void>;
  close: () => Promise<void>;
}

async function open(extraEnv: Record<string, string> = {}): Promise<Session> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-p2-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
  const ling = writeStandIn(home, askRoot);
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_ADMIN: ling, MIGHTLING_LEGACY_MIGRATION: "0", ...(out ? {} : { MIGHTLING_HIDDEN: "1" }), ...extraEnv },
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
      await closeApp(app);
      fs.rmSync(home, { recursive: true, force: true });
    },
  };
}

const appServers = (home: string) => runs(home).filter((args) => args.includes("app-server")).length;

test("4 composer: @ files, $ skills, / commands, the picker, follow-ups, approval keys, fork", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await page.locator(".approval").waitFor();

    // Enter on an empty composer approves the open approval.
    await composer.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => received(home).some((m) => m.id === "ask-1" && JSON.stringify(m.result) === JSON.stringify({ decision: "accept" }))).toBe(true);
    await expect(page.locator(".approval")).toHaveCount(0);
    // Answered once: no second answer refused by the policy.
    await expect(page.locator(".notice")).toHaveCount(0);

    // `/` at the start opens the commands.
    await composer.fill("");
    await composer.pressSequentially("/");
    await expect(page.getByRole("listbox", { name: "Commands" })).toBeVisible();
    await expect(page.getByRole("option", { name: /\/fork/ })).toBeVisible();
    await shoot("4-composer-commands.png");

    // `@` searches the thread's folder; Enter writes the path.
    await composer.fill("");
    await composer.pressSequentially("Read @cli");
    await expect(page.getByRole("listbox", { name: "Files" })).toBeVisible();
    await shoot("4-composer-mention.png");
    await page.keyboard.press("Enter");
    await expect(composer).toHaveValue("Read @fetcher/client.py ");
    expect(received(home).some((m) => m.method === "fuzzyFileSearch" && (m.params?.roots as string[])[0] === projectFolders(home).fetcher)).toBe(true);

    // `$` lists the folder's skills.
    await composer.fill("");
    await composer.pressSequentially("$re");
    await expect(page.getByRole("listbox", { name: "Skills" })).toBeVisible();
    await shoot("4-composer-skills.png");
    await page.keyboard.press("Escape");

    // The model and effort picker (Ctrl+Shift+M).
    await composer.fill("");
    await page.keyboard.press("Control+Shift+M");
    await expect(page.getByRole("menu", { name: "Model and effort" })).toBeVisible();
    await shoot("4-composer-model-picker.png");
    await page.getByRole("menuitemradio", { name: /^high/ }).click();
    await expect(page.locator(".model-chip")).toContainText("high");

    // The follow-up preference (Settings › General), then a message into a running turn waits.
    await page.keyboard.press("Control+,");
    await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
    await page.getByLabel(/Queue: it waits/).check();
    await shoot("4-settings-general.png");
    await page.keyboard.press("Escape");
    await page.locator(".thread", { hasText: "debug level" }).click();
    await expect(composer).toHaveAttribute("placeholder", /Queue a follow-up/);
    await composer.fill("Then add a line to the changelog.");
    await page.keyboard.press("Enter");
    await expect(page.locator(".queued")).toContainText("changelog");
    expect(received(home).find((m) => m.method === "thread/queue/add")?.params).toMatchObject({ threadId: "w2" });
    await shoot("4-composer-queued.png");

    // `/fork` copies the thread.
    await composer.fill("/fork");
    await composer.press("Enter");
    await expect.poll(() => received(home).some((m) => m.method === "thread/fork" && m.params?.threadId === "w2")).toBe(true);

    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});

/** A git repository in the stand-in's `fetcher` folder: one commit, then two edits and a new file. */
function scratchRepo(home: string): string {
  const root = projectFolders(home).fetcher;
  fs.writeFileSync(path.join(home, ".gitconfig"), "[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n");
  const env = { ...process.env, HOME: home, GIT_CONFIG_NOSYSTEM: "1" };
  const run = (...args: string[]) => execFileSync("git", args, { cwd: root, env, encoding: "utf8" });
  const lines = Array.from({ length: 40 }, (_, index) => `    step(${index + 1})`);
  fs.mkdirSync(path.join(root, "fetcher"), { recursive: true });
  fs.writeFileSync(path.join(root, "fetcher", "client.py"), ["def fetch(url, attempts=3):", ...lines].join("\n") + "\n");
  run("init", "-q");
  run("add", "-A");
  run("commit", "-q", "-m", "First version");
  const edited = ["def fetch(url, attempts=3):", ...lines];
  edited[2] = "    step(2)  # retried";
  edited[36] = "    step(36)  # logged at debug level";
  fs.writeFileSync(path.join(root, "fetcher", "client.py"), edited.join("\n") + "\n");
  fs.writeFileSync(path.join(root, "fetcher", "backoff.py"), "def backoff(attempt):\n    return min(2 ** attempt, 30)\n");
  return root;
}

test("6 review pane across threads: nothing of the last repository, and no Revert, until the new folder answers", async () => {
  // The folder's repositories answer 3 s late, as a Mac's did while its folder-access prompt waited
  // (20-45 s), when the pane kept the last repository's diff with Stage and Revert live.
  const { page, home, close } = await open({ MIGHTLING_TEST_GIT_REPOS_DELAY_MS: "3000" });
  scratchRepo(home);
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    // The Open menu closes on Escape and on a click outside it.
    const openMenu = page.getByRole("menu", { name: "Open" });
    await page.locator(".worktree-header").getByRole("button", { name: "Open", exact: true }).click();
    await expect(openMenu).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(openMenu).toHaveCount(0);
    await page.locator(".worktree-header").getByRole("button", { name: "Open", exact: true }).click();
    await expect(openMenu).toBeVisible();
    await page.locator(".turns").click({ position: { x: 5, y: 5 } });
    await expect(openMenu).toHaveCount(0);
    await page.keyboard.press("Control+Shift+G");
    const pane = page.getByRole("complementary", { name: "Review" });
    await expect(pane.locator(".review-status")).toHaveText("Reading this folder's repository…");
    await expect(pane.locator(".file-name")).toHaveText(["fetcher/client.py", "fetcher/backoff.py"], { timeout: 15_000 });
    await expect(pane.getByRole("button", { name: "Revert all" })).toBeVisible();
    // A comment written for this thread (§9.3: it went with the pane to the next thread until 1.7.1).
    const added = pane.locator(".code.add").first();
    await added.hover();
    await added.getByRole("button", { name: "Comment on this line" }).click();
    await pane.getByRole("textbox", { name: "Comment" }).fill("Only for the flaky retry thread.");
    await pane.getByRole("button", { name: "Add comment" }).click();
    await expect(pane.getByRole("button", { name: "Send to the composer" })).toBeVisible();
    // Another thread, outside any repository: at once, nothing of fetcher's and nothing to press.
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await expect(pane.locator(".review-status")).toHaveText("Reading this folder's repository…", { timeout: 1_000 });
    await expect(pane.locator(".file-name")).toHaveCount(0);
    for (const name of ["Revert all", "Stage all", "Revert", "Stage", "Push"]) await expect(pane.getByRole("button", { name, exact: true })).toHaveCount(0);
    await expect(pane.getByRole("textbox", { name: "Commit message" })).toHaveCount(0);
    await expect(pane.locator(".review-status")).toHaveText("This folder is not in a git repository.", { timeout: 15_000 });
    await expect(pane.getByText("Only for the flaky retry thread.")).toHaveCount(0);
    // Back on the first thread: its comment is there again, as it was left.
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await expect(pane.locator(".file-name")).toHaveText(["fetcher/client.py", "fetcher/backoff.py"], { timeout: 15_000 });
    await expect(pane.getByText("Only for the flaky retry thread.")).toBeVisible();
  } finally {
    await close();
  }
});

test("item 16: a hunk edited in place in the Unstaged scope, and an edit git refuses when the file changed meanwhile", async () => {
  const { page, home, close } = await open();
  const root = scratchRepo(home);
  const client = path.join(root, "fetcher", "client.py");
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    await page.keyboard.press("Control+Shift+G");
    const pane = page.getByRole("complementary", { name: "Review" });
    const first = pane.locator(".review-file").first();
    await expect(first.locator(".file-name")).toHaveText("fetcher/client.py");
    // Edit the first hunk: its lines as the file has them now, changed, saved into the working tree.
    await first.getByRole("button", { name: "Edit", exact: true }).first().click();
    const editor = first.getByRole("textbox", { name: /^Edit fetcher\/client\.py at line/ });
    await expect(editor).toHaveValue(/step\(2\)  # retried/);
    await editor.fill((await editor.inputValue()).replace("# retried", "# retried, at most three times"));
    await first.getByRole("button", { name: "Save" }).click();
    await expect.poll(() => fs.readFileSync(client, "utf8")).toContain("    step(2)  # retried, at most three times\n");
    expect(fs.readFileSync(client, "utf8")).toContain("    step(36)  # logged at debug level\n");
    await expect(first).toContainText("at most three times");
    // The file changes on disk while the editor is open: the patch no longer fits, git refuses it, and the file keeps the other change.
    await first.getByRole("button", { name: "Edit", exact: true }).first().click();
    await editor.fill((await editor.inputValue()).replace("at most three times", "never"));
    fs.writeFileSync(client, fs.readFileSync(client, "utf8").replace("    step(3)\n", "    step(3)  # changed elsewhere\n"));
    await first.getByRole("button", { name: "Save" }).click();
    await expect(page.locator(".notice").last()).toContainText(/patch|apply|does not/i, { timeout: 10_000 });
    expect(fs.readFileSync(client, "utf8")).toContain("# retried, at most three times");
    expect(fs.readFileSync(client, "utf8")).toContain("# changed elsewhere");
    // Not offered outside Unstaged.
    await pane.getByRole("navigation", { name: "Scope" }).getByRole("button", { name: "Staged", exact: true }).click();
    await expect(pane.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
  } finally {
    await close();
  }
});

test("6 Revert asks first, and keeps the thread whole when the turn's file changes cannot be undone", async () => {
  // One click on a first turn's Revert emptied a thread on a Mac (thread/revert changes the history
  // only, and nothing asked); now it asks, undoes the files first, and stops if they cannot be undone.
  const { page, home, close } = await open();
  scratchRepo(home);
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    const revert = page.locator(".turn").first().getByRole("button", { name: "Revert" });
    // Cancelled: nothing happens.
    page.once("dialog", (dialog) => void dialog.dismiss());
    await revert.click();
    await page.waitForTimeout(500);
    expect(received(home).some((m) => m.method === "thread/revert")).toBe(false);
    // Accepted, but the stand-in's turn diff is not what the files hold: the undo fails, said so, and
    // the thread keeps its history.
    let asked = "";
    page.once("dialog", (dialog) => {
      asked = dialog.message();
      void dialog.accept();
    });
    await revert.click();
    await expect(page.locator(".notice").last()).toContainText(/patch|apply|does not/i, { timeout: 10_000 });
    expect(asked).toMatch(/^Remove this turn( and the \d+ after it)? from the thread and undo the file changes it made\?/);
    // The stand-in's turn ran pytest: what a shell command changed is in no diff, and the question says so.
    expect(asked).toMatch(/Changes made by shell commands in (this turn|these turns) are not undone\./);
    expect(received(home).some((m) => m.method === "thread/revert")).toBe(false);
  } finally {
    await close();
  }
});

test("6 review pane: scopes, stage by hunk, comments, split view, a generated message, commit", async () => {
  const { page, home, shoot, close } = await open();
  const root = scratchRepo(home);
  const gitIn = (...args: string[]) => execFileSync("git", args, { cwd: root, env: { ...process.env, HOME: home, GIT_CONFIG_NOSYSTEM: "1" }, encoding: "utf8" });
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    await page.keyboard.press("Control+Shift+G");
    const pane = page.getByRole("complementary", { name: "Review" });
    await expect(pane).toBeVisible();
    await expect(pane.locator(".review-file")).toHaveCount(2);
    await expect(pane.locator(".file-name")).toHaveText(["fetcher/client.py", "fetcher/backoff.py"]);
    await shoot("6-review-unstaged.png");

    // Stage the first hunk of client.py only.
    await pane.locator(".review-file").first().getByRole("button", { name: "Stage hunk" }).first().click();
    await expect.poll(() => gitIn("diff", "--cached")).toContain("# retried");
    expect(gitIn("diff", "--cached")).not.toContain("debug level");
    const scopes = pane.getByRole("navigation", { name: "Scope" });
    await scopes.getByRole("button", { name: "Staged", exact: true }).click();
    await expect(pane.locator(".review-hunk")).toHaveCount(1);

    // A comment on a line, in the split view, sent to the composer.
    await pane.getByRole("button", { name: "Switch the view" }).click();
    const added = pane.locator(".diff-table.split .code.add").first();
    await added.hover();
    await added.getByRole("button", { name: "Comment on this line" }).click();
    await pane.getByRole("textbox", { name: "Comment" }).fill("Say which attempt this is in the log line.");
    await pane.getByRole("button", { name: "Add comment" }).click();
    await shoot("6-review-staged-split-comment.png");
    await pane.getByRole("button", { name: "Send to the composer" }).click();
    await expect(page.getByRole("textbox", { name: "Message", exact: true })).toHaveValue(/Please address these review comments:\n- fetcher\/client\.py:3 /);

    // A message written by the served model (the stand-in), then the commit.
    await pane.getByRole("button", { name: "Write a message" }).click();
    await expect(pane.getByRole("textbox", { name: "Commit message" })).toHaveValue(/^Re-raise the last error in the retry loop/);
    expect(received(home).find((m) => m.method === "thread/start" && m.params?.ephemeral)?.params).toMatchObject({ cwd: root, sandbox: "read-only", approvalPolicy: "never" });
    await shoot("6-review-commit-message.png");
    await pane.locator(".commit-box").getByRole("button", { name: "Commit", exact: true }).click();
    await expect.poll(() => gitIn("log", "-1", "--format=%s").trim()).toBe("Re-raise the last error in the retry loop");
    expect(gitIn("status", "--porcelain")).toContain(" M fetcher/client.py");

    // The Commit and Last turn scopes.
    await pane.getByRole("button", { name: "Switch the view" }).click();
    await scopes.getByRole("button", { name: "Commit", exact: true }).click();
    await expect(pane.locator(".file-name")).toHaveText(["fetcher/client.py"]);
    await scopes.getByRole("button", { name: "Last turn" }).click();
    await expect(pane.locator(".file-name")).toHaveText(["fetcher/client.py"]);
    await shoot("6-review-last-turn.png");
    // A thread outside any repository: nothing of fetcher's stays in the pane.
    await scopes.getByRole("button", { name: "Unstaged" }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await expect(pane.locator(".review-status")).toHaveText("This folder is not in a git repository.");
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await close();
  }
});

test("7 worktrees: a new thread in a worktree with setup, Create branch here, hand-off, actions, the environment editor", async () => {
  const { page, home, shoot, close } = await open();
  const root = scratchRepo(home);
  const env = { ...process.env, HOME: home, GIT_CONFIG_NOSYSTEM: "1" };
  const gitIn = (cwd: string, ...args: string[]) => execFileSync("git", args, { cwd, env, encoding: "utf8" }).trim();
  fs.mkdirSync(path.join(root, ".codex", "environments"), { recursive: true });
  fs.writeFileSync(path.join(root, ".codex", "environments", "environment.toml"),
    'name = "Fetcher"\n[setup]\nscript = "echo setting up in $(basename $(pwd))"\n[[actions]]\nname = "Tests"\nicon = "test"\nscript = "echo ran the tests"\n');
  gitIn(root, "add", "-A");
  gitIn(root, "commit", "-q", "-m", "Work in progress");
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(root);
    const options = page.getByLabel("Where the new thread works");
    await expect(options).toBeVisible();
    await options.getByRole("radio", { name: "Worktree" }).click();
    await options.getByRole("combobox", { name: "Local environment" }).selectOption({ label: "Fetcher" });
    await shoot("7-new-thread-worktree.png");

    // The first message makes the worktree, starts the thread there and runs the setup.
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await composer.fill("Count the retries and log the count.");
    await composer.press("Enter");
    await expect.poll(() => received(home).find((m) => m.method === "thread/start" && !m.params?.ephemeral)?.params?.cwd as string | undefined)
      .toMatch(new RegExp(`/\\.mightling/worktrees/[0-9a-f]{4}/fetcher$`));
    const checkout = received(home).find((m) => m.method === "thread/start" && !m.params?.ephemeral)!.params!.cwd as string;
    const ownerFile = path.resolve(checkout, gitIn(checkout, "rev-parse", "--git-path", "codex-thread.json"));
    await expect.poll(() => fs.existsSync(ownerFile)).toBe(true);
    expect(JSON.parse(fs.readFileSync(ownerFile, "utf8")).ownerThreadId).toMatch(/^n\d+$/);
    await expect.poll(() => received(home).find((m) => m.method === "turn/start")?.params?.cwd).toBe(checkout);
    const panel = page.getByRole("region", { name: "Bottom panel" });
    await expect(panel.locator(".panel-output")).toContainText("setting up in fetcher");
    await expect(page.locator(".chip.location")).toContainText("worktree · detached");
    await expect(page.locator(".item.agent")).toContainText("Done");

    // The agent's work, then Create branch here and the hand-off to the local checkout.
    fs.appendFileSync(path.join(checkout, "fetcher", "client.py"), "# retries counted\n");
    await page.getByRole("button", { name: "Create branch here" }).click();
    await page.getByRole("textbox", { name: "Branch name" }).fill("agent/retry-count");
    await page.getByRole("textbox", { name: "Branch name" }).press("Enter");
    await expect(page.locator(".chip.location")).toContainText("worktree · agent/retry-count");
    await shoot("7-worktree-thread.png");
    await page.getByRole("button", { name: "Hand off" }).click();
    await page.getByRole("menuitem", { name: "To the local checkout" }).click();
    await expect(page.locator(".chip.location")).toContainText("local · agent/retry-count");
    expect(gitIn(root, "symbolic-ref", "--short", "HEAD")).toBe("agent/retry-count");
    expect(fs.readFileSync(path.join(root, "fetcher", "client.py"), "utf8")).toContain("# retries counted");
    expect(gitIn(checkout, "status", "--porcelain")).toBe("");

    // An action of the environment runs in a terminal tab (item 8), in the folder the thread now works in.
    await panel.getByRole("button", { name: "Tests" }).click();
    await expect(panel.locator(".xterm-rows").filter({ hasText: "ran the tests" })).toHaveCount(1);
    await expect.poll(() => received(home).filter((m) => m.method === "command/exec").pop()?.params?.cwd).toBe(root);
    await shoot("7-handed-off-local.png");

    // The environment editor writes .mightling/ only.
    await panel.getByRole("button", { name: "Environment…" }).click();
    const editor = page.getByRole("dialog", { name: "Local environment" });
    await editor.getByRole("textbox", { name: "Action 1 script" }).fill("npm test -- --silent");
    await shoot("7-environment-editor.png");
    await editor.getByRole("button", { name: "Save" }).click();
    await expect.poll(() => fs.existsSync(path.join(root, ".mightling", "environments", "default.toml"))).toBe(true);
    expect(fs.readFileSync(path.join(root, ".mightling", "environments", "default.toml"), "utf8")).toContain("npm test -- --silent");
    expect(fs.readFileSync(path.join(root, ".codex", "environments", "environment.toml"), "utf8")).toContain('script = "echo ran the tests"');

    await page.keyboard.press("Control+,");
    await page.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Worktrees" }).click();
    await expect(page.getByRole("textbox", { name: "Worktree root" })).toBeVisible();
    await shoot("7-settings-worktrees.png");
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await close();
  }
});

test("2 shell: command menu, sidebar toggle, back and forward, font size, undo, close and reopen, view keys, a rebound shortcut", async () => {
  const { page, home, shoot, close } = await open();
  try {
    // Alt+2 and Alt+1 switch the view (Ctrl+1–9 are left for threads).
    await page.keyboard.press("Alt+2");
    await expect(page.getByRole("button", { name: "Work", exact: true })).toHaveClass(/selected/);
    // The command menu finds a thread and opens it.
    await page.keyboard.press("Control+k");
    const menu = page.getByRole("dialog", { name: "Command menu" });
    await page.getByRole("textbox", { name: "Command" }).fill("flaky");
    await shoot("2-command-menu.png");
    await page.keyboard.press("Enter");
    await expect(menu).toHaveCount(0);
    await expect(page.locator(".bar .title")).toHaveText("Fix the flaky retry test");
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    await page.locator(".thread", { hasText: "debug level" }).click();
    await expect(page.locator(".bar .title")).toHaveText("Log every retry at debug level");
    // Back and forward.
    await page.keyboard.press("Control+[");
    await expect(page.locator(".bar .title")).toHaveText("Fix the flaky retry test");
    await page.keyboard.press("Control+]");
    await expect(page.locator(".bar .title")).toHaveText("Log every retry at debug level");
    // The sidebar folds away and comes back.
    await page.keyboard.press("Control+b");
    await expect(page.locator(".work")).toHaveClass(/sidebar-hidden/);
    await shoot("2-sidebar-hidden.png");
    await page.keyboard.press("Control+b");
    await expect(page.locator(".work")).not.toHaveClass(/sidebar-hidden/);
    // Font size.
    await page.keyboard.press("Control+=");
    await expect.poll(() => page.evaluate(() => document.getElementById("root")?.style.zoom)).toBe("1.1");
    await page.keyboard.press("Control+0");
    await expect.poll(() => page.evaluate(() => document.getElementById("root")?.style.zoom)).toBe("");
    // Archive, then Ctrl+Z brings the thread back.
    await page.locator(".thread-row", { hasText: "debug level" }).hover();
    await page.locator(".thread-row", { hasText: "debug level" }).getByRole("button", { name: "Archive" }).click();
    await expect(page.locator(".thread", { hasText: "debug level" })).toHaveCount(0);
    await page.locator("body").click({ position: { x: 700, y: 400 } });
    await page.keyboard.press("Control+z");
    await expect(page.locator(".thread", { hasText: "debug level" })).toHaveCount(1);
    expect(received(home).some((m) => m.method === "thread/unarchive")).toBe(true);
    // Ctrl+W closes the review pane, never the window; Ctrl+Shift+T reopens it.
    await page.keyboard.press("Control+Shift+G");
    await expect(page.getByRole("complementary", { name: "Review" })).toBeVisible();
    await page.keyboard.press("Control+w");
    await expect(page.getByRole("complementary", { name: "Review" })).toHaveCount(0);
    await page.keyboard.press("Control+w");
    await page.keyboard.press("Control+Shift+T");
    await expect(page.getByRole("complementary", { name: "Review" })).toBeVisible();
    await page.keyboard.press("Control+w");
    // Rebind the model picker in Settings › Keyboard shortcuts, then use the new keys.
    await page.keyboard.press("Control+/");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("searchbox", { name: "Search shortcuts" }).fill("model");
    await settings.locator("tr", { hasText: "Model and effort picker" }).getByRole("button", { name: "Change" }).click();
    await page.keyboard.press("Control+Alt+m");
    await expect(settings.locator("tr", { hasText: "Model and effort picker" }).locator("kbd")).toHaveText("Ctrl+Alt+M");
    await shoot("2-keyboard-shortcuts.png");
    await page.keyboard.press("Escape");
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.keyboard.press("Control+Alt+m");
    await expect(page.getByRole("menu", { name: "Model and effort" })).toBeVisible();
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await close();
  }
});

test("3 threads: pins, attention and unread, sections, find, a ling:// link, a pop-out kept on top, keys", async () => {
  const { app, page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    // w1 has an approval waiting once it is opened: the attention mark shows in its row.
    await page.locator(".thread", { hasText: "flaky retry" }).first().click();
    await page.locator(".approval").waitFor();
    await page.locator(".thread", { hasText: "debug level" }).first().click();
    await expect(page.locator(".thread-row", { hasText: "flaky retry" }).first().locator(".attention")).toBeVisible();
    // Ctrl+Alt+A goes to the thread that needs the user.
    await page.keyboard.press("Control+Alt+a");
    await expect(page.locator(".bar .title")).toHaveText("Fix the flaky retry test");
    await page.getByRole("button", { name: "Decline" }).click();

    // Pin a thread: it leads the sidebar; Ctrl+Z unpins it.
    const row = page.locator(".thread-row", { hasText: "Compress the hero" }).first();
    await row.hover();
    await row.getByRole("button", { name: "Pin" }).click();
    await expect(page.locator(".project.pinned .thread-row")).toHaveCount(1);
    // Mark another unread and put it in a new section.
    const other = page.locator(".thread-row", { hasText: "debug level" }).first();
    await other.hover();
    await other.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: "Mark unread" }).click();
    await expect(page.locator(".thread-row.unread", { hasText: "debug level" }).first()).toBeVisible();
    await other.hover();
    await other.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: "Move to a new section…" }).click();
    await page.getByRole("textbox", { name: "Section name" }).fill("Logging");
    await page.getByRole("textbox", { name: "Section name" }).press("Enter");
    await expect(page.locator(".user-section", { hasText: "Logging" }).locator(".thread-row")).toHaveCount(1);
    await page.locator(".project.recents h2").click();
    await expect(page.locator(".project.recents .thread-row")).toHaveCount(3);
    await shoot("3-sidebar-pins-sections-unread.png");
    await page.locator(".project.recents h2").click();
    await page.locator("body").click({ position: { x: 900, y: 500 } });
    await page.keyboard.press("Control+z");
    await expect(page.locator(".project.pinned")).toHaveCount(0);

    // Find in the thread.
    await page.keyboard.press("Control+f");
    await page.getByRole("textbox", { name: "Find in this thread" }).fill("retry");
    await expect(page.locator(".find-count")).toContainText(/1 of \d+/);
    await page.keyboard.press("Enter");
    await expect(page.locator(".find-count")).toContainText(/2 of \d+/);
    await shoot("3-find-in-thread.png");
    await page.keyboard.press("Escape");

    // A ling:// link from a second instance opens what it names.
    await app.evaluate(({ app: electronApp }) => {
      electronApp.emit("second-instance", {}, ["ling-app", "ling://threads/w3"], "/", { argv: ["ling://threads/w3"] });
    });
    await expect(page.locator(".bar .title")).toHaveText("Compress the hero image");

    // A pop-out: the thread in a window of its own, kept on top, on the same app-server.
    const opened = app.waitForEvent("window");
    await page.getByRole("button", { name: "Pop out" }).click();
    const popout = await opened;
    await popout.waitForLoadState("domcontentloaded");
    await expect(popout.locator(".bar .title")).toHaveText("Compress the hero image");
    await expect(popout.locator(".work")).toHaveClass(/popout/);
    await popout.getByRole("button", { name: "Always on top" }).click();
    await expect.poll(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some((window) => window.isAlwaysOnTop()))).toBe(true);
    if (process.env.MIGHTLING_LOOK_DIR) await popout.screenshot({ path: path.join(process.env.MIGHTLING_LOOK_DIR, "3-popout.png") });
    expect(received(home).filter((m) => m.method === "initialize")).toHaveLength(1);
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await close();
  }
});

test("5 plan mode: the Plan chip and /plan send the turn in Plan mode", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    // Opened to be read: thread/read, not thread/resume (which writes to the rollout, whose time the list shows).
    await expect.poll(() => received(home).some((m) => m.method === "thread/read" && m.params?.threadId === "w3")).toBe(true);
    expect(received(home).some((m) => m.method === "thread/resume" && m.params?.threadId === "w3")).toBe(false);
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await page.getByRole("button", { name: "Plan" }).click();
    await expect(page.getByRole("button", { name: "Plan" })).toHaveAttribute("aria-pressed", "true");
    await composer.fill("Plan how to serve the hero image in three sizes.");
    await shoot("5-plan-mode.png");
    await composer.press("Enter");
    await expect.poll(() => (received(home).find((m) => m.method === "turn/start")?.params?.collaborationMode as { mode?: string } | undefined)?.mode).toBe("plan");
    // Sending loads it first: one resume, before the turn.
    const order = received(home).filter((m) => (m.method === "thread/resume" || m.method === "turn/start") && m.params?.threadId === "w3").map((m) => m.method);
    expect(order.slice(0, 2)).toEqual(["thread/resume", "turn/start"]);
    // /plan switches it off again for the next turn.
    await composer.fill("/plan");
    await composer.press("Enter");
    await expect(page.getByRole("button", { name: "Plan" })).toHaveAttribute("aria-pressed", "false");
  } finally {
    await close();
  }
});

test("9 layout: the review tab beside the thread, in full, hidden, and back", async () => {
  const { page, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+Shift+G");
    const pane = page.getByRole("complementary", { name: "Review" });
    await expect(pane.getByRole("tab", { name: /Review/ })).toBeVisible();
    await shoot("9-layout-split.png");
    await page.keyboard.press("Control+Shift+F");
    await expect(page.locator(".work")).toHaveClass(/tabs-full/);
    await expect(page.locator(".thread-pane")).toBeHidden();
    await shoot("9-layout-full.png");
    await page.keyboard.press("Control+Alt+b");
    await expect(pane).toHaveCount(0);
    await expect(page.locator(".thread-pane")).toBeVisible();
    await page.keyboard.press("Control+Shift+B");
    await expect(pane).toBeVisible();
    await expect(page.locator(".work")).not.toHaveClass(/tabs-full/);
    await pane.getByRole("button", { name: "Close the Review tab" }).click();
    await expect(pane).toHaveCount(0);
  } finally {
    await close();
  }
});

test("10 settings: Profile, Notifications, Appearance, Personalization, Archived threads, Git, Project", async () => {
  const { page, home, shoot, close } = await open();
  const day = path.join(home, ".mightling", "sessions", "2026", "10", "15");
  fs.mkdirSync(day, { recursive: true });
  fs.writeFileSync(path.join(day, "rollout-2026-10-15T09-00-00-0199aaaa.jsonl"), JSON.stringify({ type: "event_msg", payload: { type: "token_count", info: { total_token_usage: { total_tokens: 48210 } } } }) + "\n");
  try {
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    const go = (name: string) => settings.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name, exact: true }).click();
    await go("Profile");
    await expect(settings.locator(".info-rows")).toContainText("48,210");
    await shoot("10-settings-profile.png");
    await go("Notifications");
    await settings.getByLabel("Never").check();
    await go("Appearance");
    await settings.getByLabel("Accent colour").fill("#7c3aed");
    await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue("--accent"))).toBe("#7c3aed");
    await shoot("10-settings-appearance.png");
    await settings.getByRole("button", { name: "Back to Mightling's own" }).click();
    await go("Personalization");
    await settings.getByRole("textbox", { name: "Personal instructions" }).fill("Answer in British English.");
    await settings.getByRole("button", { name: "Save" }).click();
    await expect.poll(() => fs.existsSync(path.join(home, ".mightling", "AGENTS.md")) && fs.readFileSync(path.join(home, ".mightling", "AGENTS.md"), "utf8")).toBe("Answer in British English.");
    await shoot("10-settings-personalization.png");
    await go("Archived threads");
    await expect(settings).toContainText("Try a smaller hero image");
    await shoot("10-settings-archived.png");
    await settings.getByRole("button", { name: "Unarchive" }).click();
    await expect.poll(() => received(home).some((m) => m.method === "thread/unarchive" && m.params?.threadId === "old1")).toBe(true);
    await go("Git");
    await settings.getByRole("textbox", { name: "Branch prefix" }).fill("dgx/");
    await settings.getByRole("textbox", { name: "Branch prefix" }).blur();
    await shoot("10-settings-git.png");
    const prefsFile = path.join(home, ".local", "share", "dev.dreamference.mightling", "prefs.json");
    await expect.poll(() => JSON.parse(fs.readFileSync(prefsFile, "utf8")).branchPrefix).toBe("dgx/");
    expect(JSON.parse(fs.readFileSync(prefsFile, "utf8")).notifyTurns).toBe("never");
    await go("Project and terminal");
    await expect(settings.getByRole("combobox", { name: "Editor" })).toBeVisible();
  } finally {
    await close();
  }
});

test("11 skills and tools: skills with their source, MCP servers (one added), apps (one turned off)", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.getByRole("button", { name: "Skills and tools" }).click();
    const view = page.getByRole("dialog", { name: "Skills and tools" });
    await expect(view).toContainText("$release-notes");
    await expect(view).toContainText("from otheragent");
    await shoot("11-skills.png");
    await view.getByRole("button", { name: "MCP servers" }).click();
    await expect(view).toContainText("ling-code");
    await view.getByRole("button", { name: "Add an MCP server" }).click();
    await view.getByRole("textbox", { name: "Server name" }).fill("docs-search");
    await view.getByRole("textbox", { name: "Server command or URL" }).fill("ling-docs");
    await view.getByRole("textbox", { name: "Server arguments" }).fill("mcp --stdio");
    await shoot("11-mcp-add.png");
    await view.getByRole("button", { name: "Add", exact: true }).click();
    await expect.poll(() => received(home).find((m) => m.method === "config/value/write")?.params).toEqual({ keyPath: "mcp_servers.docs-search", value: { command: "ling-docs", args: ["mcp", "--stdio"] }, mergeStrategy: "upsert" });
    await view.getByRole("button", { name: "Apps" }).click();
    await expect(view).toContainText("Gmail");
    await view.getByRole("button", { name: "Turn off" }).click();
    await expect.poll(() => received(home).filter((m) => m.method === "config/value/write").pop()?.params).toMatchObject({ keyPath: "apps.gmail.enabled", value: false });
    await shoot("11-apps.png");
  } finally {
    await close();
  }
});

test("12 scheduled: Night Shift's inbox with an unread count, the queue, a task queued and one dropped, the report", async () => {
  const { page, home, shoot, close } = await open();
  const night = path.join(home, ".mightling", "night");
  const { fetcher } = projectFolders(home);
  fs.mkdirSync(path.join(night, "tasks"), { recursive: true });
  fs.mkdirSync(path.join(night, "reports"), { recursive: true });
  const task = (id: string, text: string, status: string, at: string) => JSON.stringify({ id, repo: fetcher, task: text, status, branch: `night/${id}`, test: null, on: null,
    history: [{ at: "2026-10-16T22:00:00+01:00", status: "queued" }, { at, status }], result: null });
  fs.writeFileSync(path.join(night, "tasks", "20261016-2200.json"), task("20261016-2200", "Add type hints to fetcher/client.py", "done", "2026-10-17T03:12:00+01:00"));
  fs.writeFileSync(path.join(night, "tasks", "20261016-2215.json"), task("20261016-2215", "Make the retry test table-driven", "queued", "2026-10-16T22:15:00+01:00"));
  fs.writeFileSync(path.join(night, "reports", "2026-10-17.md"), `# Night Shift, 17 October\n\n## ${fetcher}\n\n- **Add type hints to fetcher/client.py**: done on night/20261016-2200, tests pass (12 passed).\n`);
  try {
    // The app was started before the files: reopen Work so the count is read (the inbox is read on start).
    await page.reload();
    await page.locator(".thread", { hasText: "GB10" }).waitFor();
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await expect(page.locator(".scheduled-button .count")).toHaveText("1");
    await page.getByRole("button", { name: /Scheduled/ }).click();
    const view = page.getByRole("dialog", { name: "Scheduled" });
    await expect(view).toContainText("Add type hints to fetcher/client.py");
    await expect(view.locator(".night-report")).toContainText("tests pass");
    await shoot("12-scheduled.png");
    await view.getByRole("textbox", { name: "Task" }).fill("Update the changelog for 1.7");
    await view.getByRole("button", { name: "Queue" }).click();
    await expect(view.locator(".notice")).toContainText("Queued 20261017-0930");
    await view.getByRole("button", { name: "Drop" }).click();
    // (`night every list` is Scheduled reading the repeating tasks, decision 7.)
    await expect.poll(() => runs(home).filter((args) => args[0] === "night" && args[1] !== "every").map((args) => args.join(" "))).toEqual([
      "night add Update the changelog for 1.7", "night drop 20261016-2215",
    ]);
    // A finished run is unread until it is opened (DESKTOP §16 B41).
    await view.getByRole("button", { name: "Add type hints to fetcher/client.py" }).click();
    await page.keyboard.press("Escape");
    await expect(page.locator(".scheduled-button .count")).toHaveCount(0);
  } finally {
    await close();
  }
});

test("Settings › Node: the nodes on the network by name, the one in use marked, and Use for another", async () => {
  const { page, home, shoot, close } = await open();
  try {
    // Nothing browses before the page opens.
    expect(runs(home).some((args) => args[0] === "node")).toBe(false);
    await page.keyboard.press("Control+,");
    await page.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Node" }).click();
    const table = page.getByRole("table", { name: "Nodes" });
    await expect(table.locator("tr.in-use")).toContainText("spark-1");
    await expect(table).toContainText("RadixArk/Qwen3.8-27B-NVFP4");
    // Names only: no address on the page.
    await expect(page.getByRole("dialog", { name: "Settings" })).not.toContainText("192.168.");
    await shoot("node-settings.png");
    await table.locator("tr", { hasText: "spark-2" }).getByRole("button", { name: "Use" }).click();
    await expect(page.getByText("Using spark-2.")).toBeVisible();
    await expect(table.locator("tr.in-use")).toContainText("spark-2");
    await expect(page.getByRole("dialog", { name: "Settings" })).not.toContainText("192.168.");
    expect(runs(home).filter((args) => args[0] === "node").map((args) => args.join(" "))).toEqual(["node list --json", "node use spark-2", "node list --json"]);
  } finally {
    await close();
  }
});

test("13 subagents and memories: the panel of a thread's subagents with Stop, the working count, Settings › Memories", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    const panel = page.getByLabel("Subagents", { exact: true });
    await expect(panel).toContainText("Ada");
    await expect(panel).toContainText("Brin");
    await expect(panel).toContainText("1 of 2 working");
    await page.getByRole("button", { name: "Decline" }).click();
    await shoot("13-subagents.png");
    await panel.getByRole("button", { name: "Stop", exact: true }).click();
    await expect.poll(() => received(home).find((m) => m.method === "turn/interrupt")?.params).toEqual({ threadId: "s1", turnId: "ts1" });
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Memories" }).click();
    await settings.getByRole("checkbox", { name: "Memories", exact: true }).click();
    await expect.poll(() => received(home).find((m) => m.method === "config/value/write")?.params).toEqual({ keyPath: "features.memories", value: true, mergeStrategy: "upsert" });
    await shoot("13-settings-memories.png");
  } finally {
    await close();
  }
});

test("14 notifications and sleep: a finished turn notifies as Settings say, named by its thread, a click shows it; sleep is held while it runs", async () => {
  const { app, page, home, shoot, close } = await open();
  try {
    // Record notifications and sleep blocks in the main process instead of showing them.
    await app.evaluate(({ Notification, powerSaveBlocker }) => {
      const g = globalThis as unknown as { shown: string[]; titles: string[]; blocks: number; notes: Map<string, Electron.Notification> };
      g.shown = [];
      g.titles = [];
      g.blocks = 0;
      Notification.prototype.show = function show(this: Electron.Notification) {
        g.shown.push(this.body);
        g.titles.push(this.title);
        (globalThis as unknown as { last: Electron.Notification }).last = this;
      };
      const start = powerSaveBlocker.start.bind(powerSaveBlocker);
      powerSaveBlocker.start = (type) => {
        g.blocks++;
        return start(type);
      };
    });
    const shown = () => app.evaluate(() => (globalThis as unknown as { shown: string[] }).shown);
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Notifications" }).click();
    await settings.getByLabel("Always notify").check();
    // Questions have their own switch, on while approvals are, until it is set.
    const questions = settings.getByLabel("Notify when it asks a question and the window is not in front");
    await expect(questions).toBeChecked();
    await questions.uncheck();
    const prefsFile = path.join(home, ".local", "share", "dev.dreamference.mightling", "prefs.json");
    await expect.poll(() => JSON.parse(fs.readFileSync(prefsFile, "utf8")).notifyQuestions).toBe(false);
    await expect(settings.getByLabel("Notify when it waits for an approval and the window is not in front")).toBeChecked();
    await settings.getByRole("button", { name: "Send a test notification" }).click();
    await expect.poll(shown).toContain("Notifications reach you like this.");
    await shoot("14-settings-notifications.png");
    await page.keyboard.press("Escape");
    // A new thread's turn runs and finishes: sleep was held, and a notification names it.
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(projectFolders(home).website);
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await composer.fill("Shrink the hero image.");
    await composer.press("Enter");
    // It is titled with the thread (its first message, until it has a name) and says what the turn ended with.
    await expect.poll(shown).toContain("Done: the retry counter is in fetcher/client.py.");
    expect(await app.evaluate(() => (globalThis as unknown as { titles: string[] }).titles)).toContain("Shrink the hero image.");
    expect(await app.evaluate(() => (globalThis as unknown as { blocks: number }).blocks)).toBeGreaterThan(0);
    // A click on it shows the thread.
    await page.locator(".thread", { hasText: "debug level" }).click();
    await app.evaluate(() => (globalThis as unknown as { last: Electron.Notification }).last.emit("click"));
    await expect.poll(() => received(home).filter((m) => m.method === "thread/resume").map((m) => m.params?.threadId).pop()).toMatch(/^n\d+$/);
  } finally {
    await close();
  }
});

test("11 plugins and hooks: a skill turned off, a plugin installed from a local marketplace, MCP servers read again, the hooks", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.getByRole("button", { name: "Skills and tools" }).click();
    const view = page.getByRole("dialog", { name: "Skills and tools" });
    await view.locator("tr", { hasText: "$pdf-search" }).getByRole("button", { name: "Turn off" }).click();
    await expect.poll(() => received(home).find((m) => m.method === "skills/config/write")?.params).toEqual({ path: "/skills/pdf-search/SKILL.md", enabled: false });
    await view.getByRole("button", { name: "Plugins" }).click();
    await view.getByRole("button", { name: "Install" }).click();
    await expect.poll(() => received(home).find((m) => m.method === "plugin/install")?.params).toEqual({ marketplacePath: "/srv/team/marketplace.json", pluginName: "lint" });
    await shoot("11-plugins.png");
    await view.getByRole("button", { name: "MCP servers" }).click();
    await view.getByRole("button", { name: "Read the servers again" }).click();
    await expect.poll(() => received(home).some((m) => m.method === "config/mcpServer/reload")).toBe(true);
    await view.getByRole("button", { name: "Hooks" }).click();
    await expect(view).toContainText("ling ledger");
    await shoot("11-hooks.png");
    // Item 15: a project's hook nobody trusted yet is skipped, and says so; Trust shows the command first.
    await expect(view.getByRole("button", { name: "Hooks (1 to review)" })).toBeVisible();
    const audit = view.locator("tr", { hasText: "./scripts/audit.sh" });
    await expect(audit).toContainText("not trusted yet: skipped");
    await expect(view.locator("tr", { hasText: "ling ledger" })).toContainText("managed");
    let asked = "";
    page.once("dialog", (dialog) => {
      asked = dialog.message();
      void dialog.accept();
    });
    await audit.getByRole("button", { name: "Trust ./scripts/audit.sh" }).click();
    await expect.poll(() => received(home).find((m) => m.method === "config/batchWrite")?.params).toEqual({
      edits: [{ keyPath: "hooks.state", value: { "/w/app/.codex/hooks.json:post_tool_use:0:0": { trusted_hash: "sha256:audit-v1" } }, mergeStrategy: "upsert" }],
      filePath: null, expectedVersion: null, reloadUserConfig: true,
    });
    expect(asked).toContain("PostToolUse: ./scripts/audit.sh");
    await expect(audit).not.toContainText("skipped");
    await expect(audit).toContainText("on · trusted");
    await expect(view.getByRole("button", { name: "Hooks", exact: true })).toBeVisible();
    await audit.getByRole("button", { name: "Turn off" }).click();
    await expect(audit).toContainText("off · trusted");
  } finally {
    await close();
  }
});

test("13 memories held half: /memories per thread, the status, Delete", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await composer.fill("/memories off");
    await page.keyboard.press("Escape");
    await composer.press("Enter");
    await expect.poll(() => received(home).find((m) => m.method === "thread/memoryMode/set")?.params).toEqual({ threadId: "w3", mode: "disabled" });
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Memories" }).click();
    await expect(settings).toContainText("7 threads remembered");
    await shoot("13-memories-status.png");
    page.once("dialog", (dialog) => void dialog.accept());
    await settings.getByRole("button", { name: "Delete the memories" }).click();
    await expect.poll(() => received(home).some((m) => m.method === "memory/reset")).toBe(true);
  } finally {
    await close();
  }
});

test("5 goal: /goal sets the thread's goal, the row pauses, resumes and clears it", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await composer.fill("/goal The hero image is served in three sizes and the page test passes");
    await page.keyboard.press("Escape");
    await composer.press("Enter");
    const row = page.getByLabel("Goal", { exact: true });
    await expect(row).toContainText("The hero image is served in three sizes");
    await expect(row).toContainText("working on it");
    await row.getByRole("button", { name: "Pause" }).click();
    await expect(row).toContainText("paused");
    await shoot("5-goal-row.png");
    expect(received(home).filter((m) => m.method === "thread/goal/set").map((m) => m.params?.status)).toEqual(["active", "paused"]);
    await row.getByRole("button", { name: "Resume" }).click();
    await expect(row).toContainText("working on it");
    await row.getByRole("button", { name: "Clear the goal" }).click();
    await expect(page.getByLabel("Goal", { exact: true })).toHaveCount(0);
  } finally {
    await close();
  }
});

test("6 /review: the server reviews the uncommitted changes and the findings show in the thread", async () => {
  const { page, home, shoot, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await composer.fill("/review");
    await composer.press("Enter");
    await expect.poll(() => received(home).find((m) => m.method === "review/start")?.params).toEqual({ threadId: "w1", target: { type: "uncommittedChanges" }, delivery: "inline" });
    await expect(page.locator(".review-result")).toContainText("One finding.");
    await shoot("6-review-command.png");
  } finally {
    await close();
  }
});

test("8 terminal: a tab in the thread's folder under its permissions, typing, an action in its own tab, Send to the thread, Close", async () => {
  const { page, home, shoot, close } = await open();
  const root = scratchRepo(home);
  fs.mkdirSync(path.join(root, ".mightling", "environments"), { recursive: true });
  fs.writeFileSync(path.join(root, ".mightling", "environments", "default.toml"), 'name = "Fetcher"\n[[actions]]\nname = "Tests"\nicon = "test"\nscript = "echo ran the tests"\n');
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    await page.keyboard.press("Control+`");
    const panel = page.getByRole("region", { name: "Bottom panel" });
    await expect(panel.locator(".xterm-rows")).toContainText("stand-in shell in");
    // The panel fits in the window: the page never grows past it (it did, to 924 px in 860), and Settings stays on screen.
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight)).toBe(true);
    const settings = await page.getByRole("button", { name: "Settings" }).boundingBox();
    expect(settings && settings.y + settings.height <= (await page.evaluate(() => innerHeight))).toBe(true);
    const exec = received(home).find((m) => m.method === "command/exec");
    expect(exec?.params).toMatchObject({ tty: true, cwd: projectFolders(home).fetcher, permissionProfile: ":workspace", disableTimeout: true });
    await page.keyboard.type("echo hello from the tab");
    await page.keyboard.press("Enter");
    await expect(panel.locator(".xterm-rows")).toContainText("hello from the tab");
    expect(received(home).some((m) => m.method === "command/exec/write")).toBe(true);
    await shoot("8-terminal-tab.png");

    // An environment action runs in a terminal tab of its own.
    await panel.getByRole("button", { name: "Tests" }).click();
    await expect(panel.getByRole("tab", { name: "Tests" })).toBeVisible();
    await expect(panel.locator(".xterm-rows").filter({ hasText: "ran the tests" })).toHaveCount(1);
    await shoot("8-terminal-action.png");

    // Send to the thread puts the first tab's output in the composer.
    await panel.getByRole("tab", { name: "Terminal" }).click();
    await panel.getByRole("button", { name: "Send to the thread" }).click();
    await expect(page.getByRole("textbox", { name: "Message", exact: true })).toHaveValue(/Here is what my terminal in .* shows:[\s\S]*hello from the tab/);

    // Close terminates the shell.
    await panel.getByRole("button", { name: "Close Terminal" }).click();
    await expect.poll(() => received(home).some((m) => m.method === "command/exec/terminate")).toBe(true);
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
  } finally {
    await close();
  }
});

test("decision 7: a finished run opens on its result, changes and transcript, a task repeats every week, one from a thread carries its context", async () => {
  const { page, home, close } = await open();
  const night = path.join(home, ".mightling", "night");
  const { fetcher } = projectFolders(home);
  for (const folder of ["tasks", "logs", "reports"]) fs.mkdirSync(path.join(night, folder), { recursive: true });
  fs.writeFileSync(path.join(night, "tasks", "20261020-2200.json"), JSON.stringify({ id: "20261020-2200", repo: fetcher, base: "abc", task: "Log every retry at debug level", status: "done",
    branch: "night/20261020-2200", test: null, on: null, history: [{ at: "2026-10-21T03:00:00+01:00", status: "done" }], result: { branch: "night/20261020-2200", best_of: { chosen: 2 } } }));
  fs.writeFileSync(path.join(night, "logs", "20261020-2200-a2.last.txt"), "Every retry is now logged at **debug** level.\n");
  fs.writeFileSync(path.join(night, "logs", "20261020-2200-a2.diff"), "--- a/fetcher/client.py\n+++ b/fetcher/client.py\n@@ -3 +3 @@\n-    step(2)\n+    log.debug(\"retry\")\n");
  fs.writeFileSync(path.join(night, "logs", "20261020-2200-a2.jsonl"), [
    JSON.stringify({ type: "thread.started", thread_id: "w2" }),
    JSON.stringify({ type: "item.completed", item: { id: "1", type: "command_execution", command: "pytest -q", exit_code: 0 } }),
    JSON.stringify({ type: "item.completed", item: { id: "2", type: "agent_message", text: "Done: debug logging added." } }),
  ].join("\n"));
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("button", { name: /Scheduled/ }).click();
    const view = page.getByRole("dialog", { name: "Scheduled" });
    await view.getByRole("button", { name: "Log every retry at debug level" }).click();
    const details = view.locator(".night-details");
    await expect(details).toContainText("Every retry is now logged at debug level.");
    await details.getByRole("button", { name: "Changes" }).click();
    await expect(details.locator(".night-diff")).toContainText('log.debug("retry")');
    await details.getByRole("button", { name: "Transcript" }).click();
    await expect(details).toContainText("$ pytest -q  (exit 0)");
    await expect(details).toContainText("Mightling: Done: debug logging added.");

    // Every week on Friday, through ling-admin night every.
    await view.getByRole("textbox", { name: "Task" }).fill("Bump the dependencies");
    await view.getByRole("combobox", { name: "Repeat" }).selectOption("weekly");
    await view.getByRole("combobox", { name: "Day" }).selectOption("fri");
    await view.getByRole("button", { name: "Repeat", exact: true }).click();
    await expect.poll(() => runs(home).find((args) => args[1] === "every" && args[2] === "add")).toEqual(["night", "every", "add", "--weekly", "fri", "--", "Bump the dependencies"]);
    await expect(view.locator("tr", { hasText: "Bump the dependencies" })).toContainText("every Friday");
    await view.locator("tr", { hasText: "Bump the dependencies" }).getByRole("button", { name: "Stop" }).click();
    await expect(view.locator("tr", { hasText: "Bump the dependencies" })).toHaveCount(0);

    // The finished run opened as a thread.
    await details.getByRole("button", { name: "Open as a thread" }).click();
    await expect(view).toHaveCount(0);
    await expect(page.locator(".bar .title")).toHaveText("Log every retry at debug level");

    // From the thread now shown: tonight's task carries what was said in it.
    await page.getByRole("button", { name: /Scheduled/ }).click();
    await expect(view.getByRole("checkbox", { name: /With what was said in/ })).toBeChecked();
    await view.getByRole("textbox", { name: "Task" }).fill("Make it configurable");
    await view.getByRole("button", { name: "Queue" }).click();
    await expect.poll(() => runs(home).filter((args) => args[0] === "night" && args[1] === "add").map((args) => args[args.length - 1]).pop() ?? "")
      .toMatch(/^Make it configurable\n\nContext: this was asked in the conversation "Log every retry at debug level"/);
  } finally {
    await close();
  }
});

