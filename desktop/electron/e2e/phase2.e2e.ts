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

async function open(): Promise<Session> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-p2-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  const { fetcher, website } = projectFolders(home);
  for (const folder of [path.join(askRoot, "q-1a2b3c"), fetcher, website]) fs.mkdirSync(folder, { recursive: true });
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

    // An action of the environment runs in the bottom panel, in the folder the thread now works in.
    await panel.getByRole("button", { name: "Tests" }).click();
    await expect(panel.locator(".panel-output")).toContainText("ran the tests");
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
    const composer = page.getByRole("textbox", { name: "Message", exact: true });
    await page.getByRole("button", { name: "Plan" }).click();
    await expect(page.getByRole("button", { name: "Plan" })).toHaveAttribute("aria-pressed", "true");
    await composer.fill("Plan how to serve the hero image in three sizes.");
    await shoot("5-plan-mode.png");
    await composer.press("Enter");
    await expect.poll(() => (received(home).find((m) => m.method === "turn/start")?.params?.collaborationMode as { mode?: string } | undefined)?.mode).toBe("plan");
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
    await go("Project");
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
    await expect.poll(() => runs(home).filter((args) => args[0] === "night").map((args) => args.join(" "))).toEqual([
      "night add Update the changelog for 1.7", "night drop 20261016-2215",
    ]);
    await page.keyboard.press("Escape");
    await expect(page.locator(".scheduled-button .count")).toHaveCount(0);
  } finally {
    await close();
  }
});

test("13 subagents and memories: the panel of a thread's subagents with Stop, the working count, Settings › Memories", async () => {
test("11 plugins and hooks: a skill turned off, a plugin installed from a local marketplace, MCP servers read again, the hooks", async () => {
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

test("14 notifications and sleep: a finished turn notifies as Settings say, a click shows its thread; sleep is held while it runs", async () => {
  const { app, page, home, shoot, close } = await open();
  try {
    // Record notifications and sleep blocks in the main process instead of showing them.
    await app.evaluate(({ Notification, powerSaveBlocker }) => {
      const g = globalThis as unknown as { shown: string[]; blocks: number; notes: Map<string, Electron.Notification> };
      g.shown = [];
      g.blocks = 0;
      Notification.prototype.show = function show(this: Electron.Notification) {
        g.shown.push(this.body);
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
    await expect.poll(shown).toContain("A turn finished.");
    expect(await app.evaluate(() => (globalThis as unknown as { blocks: number }).blocks)).toBeGreaterThan(0);
    // A click on it shows the thread.
    await page.locator(".thread", { hasText: "debug level" }).click();
    await app.evaluate(() => (globalThis as unknown as { last: Electron.Notification }).last.emit("click"));
    await expect.poll(() => received(home).filter((m) => m.method === "thread/resume").map((m) => m.params?.threadId).pop()).toMatch(/^n\d+$/);
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
  } finally {
    await close();
  }
});
