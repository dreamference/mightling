// A scripted stand-in for `ling app-server` for the Phase 2 end-to-end tests (DESKTOP_PARITY §5):
// made-up threads in made-up folders under a scratch HOME, the methods the window's features call
// answered from a script, every line it reads appended to `received.jsonl` and every command line
// it is run with to `runs.jsonl`. Never the real `ling`, never the model server.
import fs from "node:fs";
import path from "node:path";

const thread = (id: string, cwd: string, name: string | null, preview: string, updatedAt: number, turns: unknown[] = []) => ({
  id, cwd, name, preview, turns, status: { type: "idle" }, ephemeral: false, parentThreadId: null,
  createdAt: updatedAt, updatedAt, recencyAt: null, model: "qwen3.8-27b", modelProvider: "local",
});

const text = (value: string) => [{ type: "text", text: value, text_elements: [] }];

export const DIFF = `@@ -12,9 +12,12 @@ def fetch(url, attempts=3):
     for attempt in range(attempts):
         try:
             return session.get(url, timeout=10)
-        except ConnectionError:
-            time.sleep(1)
+        except ConnectionError as error:
+            if attempt == attempts - 1:
+                raise
+            time.sleep(backoff(attempt))
     return None`;

const TURN_DIFF = `diff --git a/fetcher/client.py b/fetcher/client.py\n--- a/fetcher/client.py\n+++ b/fetcher/client.py\n${DIFF}\n`;

/** What the stand-in's model answers when the window asks it for a commit message. */
export const COMMIT_MESSAGE = "Re-raise the last error in the retry loop\n\nThe loop swallowed it, so callers saw None.";

/** The made-up projects: `fetcher` and `website` under `<home>/src`. */
export function projectFolders(home: string): { fetcher: string; website: string } {
  return { fetcher: path.join(home, "src", "fetcher"), website: path.join(home, "src", "website") };
}

export function threads(home: string, askRoot: string) {
  const ago = (minutes: number) => Math.floor(Date.now() / 1000) - minutes * 60;
  const { fetcher, website } = projectFolders(home);
  return [
    thread("w1", fetcher, "Fix the flaky retry test", "The retry test fails one run in ten", ago(12), [{
      id: "t1", status: "completed", error: null, items: [
        { type: "userMessage", id: "u1", content: text("The retry test fails one run in ten. Find out why and fix it, without making the test slower.") },
        { type: "commandExecution", id: "c1", command: "pytest tests/test_fetch.py -q -k retry", cwd: fetcher, processId: null, status: "completed",
          commandActions: [], exitCode: 1, durationMs: 2140, aggregatedOutput: "F.\nFAILED tests/test_fetch.py::test_retry_gives_up - ConnectionError\n1 failed, 1 passed in 2.11s" },
        { type: "fileChange", id: "f1", status: "completed", changes: [{ path: "fetcher/client.py", kind: { type: "update", move_path: null }, diff: DIFF }] },
        { type: "agentMessage", id: "a1", text: "The loop swallowed the last `ConnectionError`. Now the last attempt re-raises, and the pause comes from `backoff()`, which the test patches to zero." },
      ],
    }]),
    thread("w2", fetcher, "Log every retry at debug level", "", ago(3), [{
      id: "t2", status: "inProgress", error: null, items: [
        { type: "userMessage", id: "u2", content: text("Log every retry at debug level, with the URL and the error.") },
        { type: "reasoning", id: "r2", summary: ["Finding where the retries happen."], content: [] },
        { type: "commandExecution", id: "c2", command: "rg -n \"retry\" fetcher/", cwd: fetcher, processId: null, status: "inProgress",
          commandActions: [], exitCode: null, durationMs: null, aggregatedOutput: "fetcher/client.py:12:    for attempt in range(attempts):\n" },
      ],
    }]),
    thread("w3", website, "Compress the hero image", "", ago(26 * 60)),
    thread("q1", path.join(askRoot, "q-1a2b3c"), "Why does the GB10 share memory with the GPU?", "", ago(40), [{
      id: "tq", status: "completed", error: null, items: [
        { type: "userMessage", id: "uq", content: text("Why does the GB10 share memory with the GPU?") },
        { type: "agentMessage", id: "aq", text: "Because the CPU and the GPU address **one pool** of LPDDR5X." },
      ],
    }]),
  ];
}

const FILES = ["fetcher/client.py", "fetcher/__init__.py", "fetcher/backoff.py", "tests/test_fetch.py", "README.md", "pyproject.toml"];

/** Writes the stand-in `ling` into `home` and returns its path. */
export function writeStandIn(home: string, askRoot: string): string {
  const ling = path.join(home, "ling");
  const { fetcher } = projectFolders(home);
  fs.writeFileSync(ling, `#!/usr/bin/env node
const fs = require("fs");
fs.appendFileSync(${JSON.stringify(path.join(home, "runs.jsonl"))}, JSON.stringify(process.argv.slice(2)) + "\\n");
if (process.argv[2] === "airgapped") { console.log("Airgapped: off (default)"); process.exit(0); }
if (process.argv[2] === "prompt") { process.stdout.write("THE ASK PROMPT"); process.exit(0); }
const threads = ${JSON.stringify(threads(home, askRoot))};
const files = ${JSON.stringify(FILES)};
const fetcher = ${JSON.stringify(fetcher)};
const log = ${JSON.stringify(path.join(home, "received.jsonl"))};
const say = (message) => console.log(JSON.stringify(message));
const usage = { totalTokens: 41210, inputTokens: 39000, cachedInputTokens: 30000, cacheWriteInputTokens: 0, outputTokens: 2210, reasoningOutputTokens: 800 };
const queues = {};
let asked = false;
let next = 1;
const model = { id: "qwen3.8-27b", model: "qwen3.8-27b", displayName: "qwen3.8-27b", description: "The model this machine serves",
  hidden: false, isDefault: true, supportedReasoningEfforts: ["low", "medium", "high"].map((e) => ({ reasoningEffort: e, description: "" })),
  defaultReasoningEffort: "medium", inputModalities: ["text"], upgrade: null, upgradeInfo: null, availabilityNux: null, modelSpecialty: null,
  supportsPersonality: false, multiAgentVersion: null, additionalSpeedTiers: [], serviceTiers: [], defaultServiceTier: null, availableAccessPrograms: null };
const skills = [
  { name: "pdf-search", description: "Search inside PDF files the user has", shortDescription: "Search inside PDFs", path: "/skills/pdf-search/SKILL.md", scope: "user", enabled: true, pluginId: null },
  { name: "release-notes", description: "Write release notes from the git log", path: "/skills/release-notes/SKILL.md", scope: "repo", enabled: true, pluginId: null },
  { name: "night-shift", description: "Queue a task for tonight", path: "/skills/night-shift/SKILL.md", scope: "user", enabled: true, pluginId: null },
  { name: "slides", description: "Make a slide deck from notes", path: "/home/u/.mightling/skills/from-otheragent/slides/SKILL.md", scope: "user", enabled: true, pluginId: null },
];
require("readline").createInterface({ input: process.stdin }).on("line", (line) => {
  fs.appendFileSync(log, line + "\\n");
  const m = JSON.parse(line);
  if (m.id === undefined || !m.method) return;
  const p = m.params || {};
  const answer = (result) => say({ id: m.id, result });
  switch (m.method) {
    case "initialize": return answer({ userAgent: "stand-in" });
    case "thread/list":
      if (p.archived) return answer({ data: [{ ...threads[0], id: "old1", name: "Try a smaller hero image", turns: [] }], nextCursor: null });
      return answer({ data: threads.map((t) => ({ ...t, turns: [] })), nextCursor: null });
    case "permissionProfile/list": return answer({ data: [":read-only", ":workspace", ":danger-full-access"].map((id) => ({ id, allowed: true })) });
    case "model/list": return answer({ data: [model], nextCursor: null });
    case "collaborationMode/list": return answer({ data: [{ name: "Default", mode: "default", model: null, reasoning_effort: null }, { name: "Plan", mode: "plan", model: null, reasoning_effort: null }] });
    case "skills/list": return answer({ data: (p.cwds || []).map((cwd) => ({ cwd, skills, errors: [] })) });
    case "fuzzyFileSearch": {
      const q = String(p.query || "").toLowerCase();
      const found = files.filter((f) => f.toLowerCase().includes(q)).map((f, i) => ({ root: p.roots[0], path: f, match_type: "file", file_name: f.split("/").pop(), score: 100 - i, indices: null }));
      return answer({ files: found });
    }
    case "thread/resume": {
      const found = threads.find((t) => t.id === p.threadId);
      if (!found) return say({ id: m.id, error: { code: -32600, message: "no such thread" } });
      answer({ thread: found });
      if (found && found.id === "w1") {
        say({ method: "turn/diff/updated", params: { threadId: "w1", turnId: "t1", diff: ${JSON.stringify(TURN_DIFF)} } });
        say({ method: "thread/tokenUsage/updated", params: { threadId: "w1", turnId: "t1", tokenUsage: { total: usage, last: usage, modelContextWindow: 131072 } } });
        if (!asked) {
          asked = true;
          say({ id: "ask-1", method: "item/commandExecution/requestApproval", params: { threadId: "w1", turnId: "t1", itemId: "c9",
            command: "pytest tests/test_fetch.py -q", reason: "Run the whole file to check nothing else depends on the old behaviour." } });
        }
      }
      return;
    }
    case "thread/archive": return answer({});
    case "thread/unarchive": return answer({ thread: { ...(threads.find((t) => t.id === p.threadId) || { ...threads[0], id: p.threadId, name: "Try a smaller hero image" }), turns: [] } });
    case "thread/name/set": {
      const found = threads.find((t) => t.id === p.threadId);
      if (found) found.name = p.name;
      return answer({});
    }
    case "thread/queue/list": return answer({ data: queues[p.threadId] || [], nextCursor: null });
    case "thread/queue/add": {
      const item = { id: "q" + next++, input: p.input, clientUserMessageId: p.clientUserMessageId };
      (queues[p.threadId] = queues[p.threadId] || []).push(item);
      answer({ queuedSubmission: item });
      return say({ method: "thread/queue/changed", params: { threadId: p.threadId } });
    }
    case "thread/queue/delete":
      queues[p.threadId] = (queues[p.threadId] || []).filter((q) => q.id !== p.queuedSubmissionId);
      answer({});
      return say({ method: "thread/queue/changed", params: { threadId: p.threadId } });
    case "thread/fork": {
      const source = threads.find((t) => t.id === p.threadId);
      const copy = { ...source, id: source.id + "-fork", name: (source.name || "") + " (fork)", updatedAt: Math.floor(Date.now() / 1000) };
      threads.push(copy);
      return answer({ thread: copy, model: "qwen3.8-27b", modelProvider: "local", cwd: copy.cwd });
    }
    case "thread/start": {
      const id = (p.ephemeral ? "e" : "n") + next++;
      const started = { id, cwd: p.cwd, name: null, preview: "", turns: [], status: { type: "idle" }, ephemeral: !!p.ephemeral, parentThreadId: null,
        createdAt: Math.floor(Date.now() / 1000), updatedAt: Math.floor(Date.now() / 1000), recencyAt: null, model: "qwen3.8-27b", modelProvider: "local" };
      if (!p.ephemeral) threads.push(started);
      return answer({ thread: started, model: "qwen3.8-27b", modelProvider: "local", cwd: p.cwd });
    }
    case "turn/start": {
      const id = "turn-" + next++;
      if (String(p.threadId).startsWith("n")) {
        // A thread the test started: the turn runs briefly and answers.
        answer({ turn: { id, items: [], status: "inProgress", error: null } });
        say({ method: "turn/started", params: { threadId: p.threadId, turn: { id, items: [], status: "inProgress", error: null } } });
        return setTimeout(() => {
          say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { type: "agentMessage", id: "m" + id, text: "Done: the retry counter is in fetcher/client.py." } } });
          say({ method: "turn/completed", params: { threadId: p.threadId, turn: { id, items: [], status: "completed", error: null } } });
        }, 300);
      }
      if (String(p.threadId).startsWith("e")) {
        answer({ turn: { id, items: [], status: "inProgress", error: null } });
        say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { type: "agentMessage", id: "m" + id, text: ${JSON.stringify(COMMIT_MESSAGE)} } } });
        return say({ method: "turn/completed", params: { threadId: p.threadId, turn: { id, items: [], status: "completed", error: null } } });
      }
      answer({ turn: { id, items: [], status: "inProgress", error: null } });
      return say({ method: "turn/started", params: { threadId: p.threadId, turn: { id, items: [], status: "inProgress", error: null } } });
    }
    case "turn/steer": return answer({ turnId: p.expectedTurnId });
    case "app/list": return answer({ data: [{ id: "gmail", name: "Gmail", description: "Search and read your mail", logoUrl: null, logoUrlDark: null, iconAssets: null, iconDarkAssets: null,
      distributionChannel: null, branding: null, appMetadata: null, labels: null, installUrl: null, isAccessible: true, isEnabled: true, pluginDisplayNames: [] }], nextCursor: null });
    case "config/value/write": return answer({ status: "ok", version: "1", filePath: "/x", overriddenMetadata: null });
    case "mcpServerStatus/list": return answer({ data: [{ name: "ling-code", tools: { def: {}, refs: {}, callers: {} }, toolsError: null }], nextCursor: null });
    default: return answer({ data: [] });
  }
});
`);
  fs.chmodSync(ling, 0o755);
  return ling;
}

export const received = (home: string): { id?: unknown; method?: string; params?: Record<string, unknown>; result?: unknown }[] => {
  try {
    return fs.readFileSync(path.join(home, "received.jsonl"), "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
  } catch {
    return [];
  }
};

export const runs = (home: string): string[][] => {
  try {
    return fs.readFileSync(path.join(home, "runs.jsonl"), "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
  } catch {
    return [];
  }
};
