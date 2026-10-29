// The look, as pictures: Ask (empty and with a question) and Work (a thread with a command, a diff
// and an approval) in both themes, the theme choice left at System and the system's theme set dark
// then light (Electron's nativeTheme, as the system would), against a scripted
// stand-in for `ling app-server` whose threads are made up for the purpose. Not a check: it runs only when
// MIGHTLING_LOOK_DIR names a folder for the PNGs (a person compares them; DESKTOP_PARITY §3), and
// needs a display and the Vite build like the other end-to-end tests.
//   MIGHTLING_LOOK_DIR=/tmp/look npm run e2e -- look
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "@playwright/test";

import { closeApp, closeLeftovers, launch } from "./apps";

const main = path.join(__dirname, "..", ".vite", "build", "early-bootstrap.cjs");
const out = process.env.MIGHTLING_LOOK_DIR;

test.skip(!out || !process.env.DISPLAY || !fs.existsSync(main), "needs MIGHTLING_LOOK_DIR, a display and the Vite build");
test.afterEach(closeLeftovers);

const ANSWER = `The GB10 has **one pool of LPDDR5X memory** that the CPU and the GPU both address, so there is no copy over a bus before the GPU can use a tensor.

## What that means for a model server

- A 27B model in NVFP4 takes about 16 GB, wherever it "lives".
- The KV cache grows into the same 128 GB the desktop uses.
- Running out of memory can stall the whole machine, not only the GPU.

### Checking the pressure

\`\`\`bash
cat /proc/pressure/memory
nvidia-smi --query-gpu=memory.used --format=csv
\`\`\`

Keep an eye on \`some avg10\`: above 20 the host is already waiting on memory.`;

const DIFF = `@@ -12,9 +12,12 @@ def fetch(url, attempts=3):
     for attempt in range(attempts):
         try:
             return session.get(url, timeout=10)
-        except ConnectionError:
-            time.sleep(1)
+        except ConnectionError as error:
+            if attempt == attempts - 1:
+                raise
+            time.sleep(backoff(attempt))
+            log.debug("retrying %s after %s", url, error)
     return None`;

const thread = (id: string, cwd: string, name: string | null, preview: string, updatedAt: number, turns: unknown[] = []) => ({
  id, cwd, name, preview, turns, status: { type: "idle" }, ephemeral: false, parentThreadId: null,
  createdAt: updatedAt, updatedAt, recencyAt: null, model: "qwen3.8-27b", modelProvider: "local",
});

const text = (value: string) => [{ type: "text", text: value, text_elements: [] }];

function threads(home: string, askRoot: string) {
  const ago = (minutes: number) => Math.floor(Date.now() / 1000) - minutes * 60;
  const project = path.join(home, "src", "fetcher");
  const site = path.join(home, "src", "website");
  return [
    thread("w1", project, "Fix the flaky retry test", "The retry test fails one run in ten", ago(12), [{
      id: "t1", status: "completed", error: null, items: [
        { type: "userMessage", id: "u1", content: text("The retry test fails one run in ten. Find out why and fix it, without making the test slower.") },
        { type: "reasoning", id: "r1", summary: ["Reading the test and the retry loop."], content: [] },
        { type: "commandExecution", id: "c1", command: "pytest tests/test_fetch.py -q -k retry", cwd: project, processId: null, status: "completed",
          commandActions: [], exitCode: 1, durationMs: 2140,
          aggregatedOutput: "F.\nFAILED tests/test_fetch.py::test_retry_gives_up - ConnectionError\n1 failed, 1 passed in 2.11s" },
        { type: "fileChange", id: "f1", status: "completed", changes: [{ path: "fetcher/client.py", kind: { type: "update", move_path: null }, diff: DIFF }] },
        { type: "agentMessage", id: "a1", text: "The loop swallowed the last `ConnectionError` and returned `None`, so the test saw a missing response instead of the error it expects.\n\nNow the last attempt re-raises, and the pause between attempts comes from `backoff()`, which the test already patches to zero, so it is no slower." },
      ],
    }]),
    thread("w2", project, "Log every retry at debug level", "", ago(3 * 60)),
    thread("w3", site, "Dark theme for the docs", "", ago(26 * 60)),
    thread("w4", site, null, "Compress the hero image", ago(4 * 24 * 60)),
    thread("q1", path.join(askRoot, "q-1a2b3c"), "Why does the GB10 share memory with the GPU?", "", ago(40), [{
      id: "t2", status: "completed", error: null, items: [
        { type: "userMessage", id: "u2", content: text("Why does the GB10 share memory with the GPU, and what does that mean for running a model server on it?") },
        { type: "agentMessage", id: "a2", text: ANSWER },
      ],
    }]),
    thread("q2", path.join(askRoot, "q-4d5e6f"), "Best way to back up a home folder", "", ago(2 * 24 * 60)),
  ];
}

/** A stand-in `ling` that lists the threads above, resumes them, and asks to run a command in w1. */
function standIn(home: string, askRoot: string): string {
  const ling = path.join(home, "ling");
  fs.writeFileSync(ling, `#!/usr/bin/env node
const threads = ${JSON.stringify(threads(home, askRoot))};
if (process.argv[2] === "airgapped") { console.log('{"level":"off","tier":"default","source":"default","invalid":[]}'); process.exit(0); }
if (process.argv[2] === "prompt") { process.stdout.write("THE ASK PROMPT"); process.exit(0); }
const say = (message) => console.log(JSON.stringify(message));
const usage = { totalTokens: 41210, inputTokens: 39000, cachedInputTokens: 30000, cacheWriteInputTokens: 0, outputTokens: 2210, reasoningOutputTokens: 800 };
let asked = false;
require("readline").createInterface({ input: process.stdin }).on("line", (line) => {
  const m = JSON.parse(line);
  if (m.id === undefined || !m.method) return;
  if (m.method === "initialize") return say({ id: m.id, result: { userAgent: "stand-in" } });
  if (m.method === "thread/list") return say({ id: m.id, result: { data: threads.map((t) => ({ ...t, turns: [] })), nextCursor: null } });
  if (m.method === "permissionProfile/list") return say({ id: m.id, result: { data: [":read-only", ":workspace", ":danger-full-access"].map((id) => ({ id, allowed: true })) } });
  // A thread that is not running is read, not resumed (store.ts, 2026-10-19).
  if (m.method === "thread/read") return say({ id: m.id, result: { thread: threads.find((t) => t.id === m.params.threadId) } });
  if (m.method === "thread/resume") {
    const found = threads.find((t) => t.id === m.params.threadId);
    say({ id: m.id, result: { thread: found } });
    if (found.id === "w1") {
      say({ method: "thread/tokenUsage/updated", params: { threadId: "w1", turnId: "t1", tokenUsage: { total: usage, last: usage, modelContextWindow: 131072 } } });
      if (!asked) {
        asked = true;
        say({ id: "ask-1", method: "item/commandExecution/requestApproval", params: { threadId: "w1", turnId: "t1", itemId: "c2",
          command: "pytest tests/test_fetch.py -q", reason: "Run the whole file to check nothing else depends on the old behaviour." } });
      }
    }
    return;
  }
  say({ id: m.id, result: { data: [] } });
});
`);
  fs.chmodSync(ling, 0o755);
  return ling;
}

test("the look", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-look-"));
  const codexHome = path.join(home, ".mightling");
  const askRoot = path.join(codexHome, "ask");
  for (const folder of [path.join(askRoot, "q-1a2b3c"), path.join(askRoot, "q-4d5e6f"), path.join(home, "src", "fetcher"), path.join(home, "src", "website")]) {
    fs.mkdirSync(folder, { recursive: true });
  }
  fs.writeFileSync(path.join(codexHome, "model_catalog.json"), JSON.stringify({ models: [{ slug: "qwen3.8-27b-nvfp4" }] }));
  const ling = standIn(home, askRoot);
  fs.mkdirSync(out!, { recursive: true });
  const app = await launch({
    args: [main],
    env: { ...process.env, HOME: home, CODEX_HOME: codexHome, MIGHTLING_BIN: ling, MIGHTLING_LEGACY_MIGRATION: "0" },
  });
  try {
    const page = await app.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    // No transitions: a window the compositor thinks is covered can leave one at its first frame.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator(".thread", { hasText: "GB10" }).waitFor({ timeout: 30_000 });
    // Each picture with the pointer parked at the foot of the sidebar, over nothing.
    const shoot = async (name: string) => {
      await page.mouse.move(40, 840);
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(out!, name) });
    };
    // System follows the system's theme: dark first, then light (theme.e2e.ts checks the choice itself).
    for (const theme of ["dark", "light"] as const) {
      await app.evaluate(({ nativeTheme }, source) => { nativeTheme.themeSource = source; }, theme);
      await page.waitForFunction((wanted) => document.documentElement.dataset.theme === wanted, theme);
      await page.getByRole("button", { name: "Ask", exact: true }).click();
      await page.getByRole("button", { name: "New question" }).click();
      await shoot(`ask-empty-${theme}.png`);
      await page.locator(".thread", { hasText: "GB10" }).click();
      await shoot(`ask-${theme}.png`);
      await page.getByRole("button", { name: "Work", exact: true }).click();
      await page.locator(".thread", { hasText: "flaky retry" }).click();
      await shoot(`work-${theme}.png`);
    }
    // A phone's width, as `ling web` shows it: the sidebar becomes a drawer.
    await app.evaluate(({ BrowserWindow }) => {
      const [window] = BrowserWindow.getAllWindows();
      window.setMinimumSize(360, 600);
      window.setSize(400, 860);
    });
    await shoot("work-narrow-light.png");
    await app.evaluate(({ nativeTheme }) => { nativeTheme.themeSource = "dark"; });
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    await shoot("work-narrow-dark.png");
  } finally {
    await closeApp(app);
    fs.rmSync(home, { recursive: true, force: true });
  }
});
