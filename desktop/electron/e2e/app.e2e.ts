// The app as a whole: Electron starts, the app window's page is served from `app://`, the preload
// exposes the one bridge object, and the page starts a scripted stand-in for `ling app-server`
// through it; an Ask question gets its prompt and folder from the main process.
// Runs against the Vite build in `.vite/` (`npm run package` first); windows are hidden
// (`MIGHTLING_HIDDEN=1`) so a test does not take the screen.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { _electron as electron, expect, test } from "@playwright/test";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.js");

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");

/**
 * A stand-in `ling`: composes the `ask` prompt, reports the air-gap level, and as the app-server
 * answers `initialize`, lists no threads, starts a thread in the folder it is given, and records
 * every line it reads in `received.jsonl`.
 */
function standIn(home: string): string {
  const ling = path.join(home, "ling");
  fs.writeFileSync(ling, `#!/usr/bin/env node
const fs = require("fs");
if (process.argv[2] === "airgapped") { console.log("Airgapped: off (default)"); process.exit(0); }
if (process.argv[2] === "prompt") { process.stdout.write("THE ASK PROMPT"); process.exit(0); }
const log = ${JSON.stringify(path.join(home, "received.jsonl"))};
const rl = require("readline").createInterface({ input: process.stdin });
rl.on("line", (line) => {
  fs.appendFileSync(log, line + "\\n");
  const m = JSON.parse(line);
  if (m.method === "initialize") console.log(JSON.stringify({ id: m.id, result: { userAgent: "stand-in" } }));
  else if (m.method === "thread/start") console.log(JSON.stringify({ id: m.id, result: { thread: {
    id: "thread-1", cwd: m.params.cwd, preview: "", name: null, turns: [], status: { type: "idle" }, ephemeral: false,
    parentThreadId: null, createdAt: 1, updatedAt: 1, recencyAt: null, model: "m", modelProvider: "p" } } }));
  else if (m.method === "turn/start") console.log(JSON.stringify({ id: m.id, result: { turn: { id: "turn-1", items: [], status: "inProgress", error: null } } }));
  else if (m.id !== undefined && m.method) console.log(JSON.stringify({ id: m.id, result: { data: [] } }));
});
`);
  fs.chmodSync(ling, 0o755);
  return ling;
}

const received = (home: string): { method?: string; params?: Record<string, unknown> }[] => {
  try {
    return fs.readFileSync(path.join(home, "received.jsonl"), "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
  } catch {
    return [];
  }
};

test("Work opens on app:// and reaches the agent through electronBridge", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-e2e-"));
  const ling = standIn(home);
  const app = await electron.launch({
    args: [main, "--work"],
    env: { ...process.env, HOME: home, CODEX_HOME: path.join(home, ".mightling"), MIGHTLING_BIN: ling, MIGHTLING_HIDDEN: "1", MIGHTLING_LEGACY_MIGRATION: "0" },
  });
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    // Asked for Work, the window opens on Work; Ask is one click away.
    expect(page.url()).toBe("app://-/index.html#work");
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

test("Ask in the app window: the main process composes the prompt, makes the folder and takes the attachment", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-e2e-ask-"));
  const ling = standIn(home);
  const codexHome = path.join(home, ".mightling");
  const app = await electron.launch({
    args: [main, "--work"],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_HIDDEN: "1", MIGHTLING_LEGACY_MIGRATION: "0" },
  });
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await expect.poll(async () => page.evaluate(() => document.body.innerText), { timeout: 30_000 }).toContain("Work");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    expect(page.url()).toBe("app://-/index.html#");
    await page.locator("input[type=file]").setInputFiles({ name: "shot.png", mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47]) });
    await page.getByPlaceholder("Ask anything…").fill("What is in this picture?");
    await page.getByRole("button", { name: "Send" }).click();
    await expect.poll(() => received(home).some((m) => m.method === "turn/start"), { timeout: 30_000 }).toBe(true);
    const start = received(home).find((m) => m.method === "thread/start")!;
    const askRoot = fs.realpathSync(path.join(codexHome, "ask"));
    // The page named the prompt; the main process sent its text, a fresh folder and the sandbox.
    expect(start.params!.baseInstructions).toBe("THE ASK PROMPT");
    expect(start.params!.prompt).toBeUndefined();
    expect(start.params!.sandbox).toBe("workspace-write");
    const folder = start.params!.cwd as string;
    expect(path.dirname(folder)).toBe(askRoot);
    // The folder is named after the thread, and the image is in it, sent as `localImage`.
    expect(fs.realpathSync(path.join(askRoot, "thread-1"))).toBe(folder);
    const turn = received(home).find((m) => m.method === "turn/start")!;
    const input = turn.params!.input as { type: string; path?: string }[];
    expect(input).toContainEqual({ type: "localImage", path: path.join(folder, "shot.png") });
    expect(fs.readFileSync(path.join(folder, "shot.png"))).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  } finally {
    await app.close();
    fs.rmSync(home, { recursive: true, force: true });
  }
});
