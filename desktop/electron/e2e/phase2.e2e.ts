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
    await expect(page.getByRole("textbox", { name: "Worktree root" })).toBeVisible();
    await shoot("7-settings-worktrees.png");
    expect(runs(home).filter((args) => args.includes("app-server"))).toHaveLength(1);
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
