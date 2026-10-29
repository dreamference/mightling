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
    { ...thread("w1", fetcher, "Fix the flaky retry test", "The retry test fails one run in ten", ago(12), [{
      id: "t1", status: "completed", error: null, items: [
        { type: "userMessage", id: "u1", content: text("The retry test fails one run in ten. Find out why and fix it, without making the test slower.") },
        { type: "commandExecution", id: "c1", command: "pytest tests/test_fetch.py -q -k retry", cwd: fetcher, processId: null, status: "completed",
          commandActions: [], exitCode: 1, durationMs: 2140, aggregatedOutput: "F.\nFAILED tests/test_fetch.py::test_retry_gives_up - ConnectionError\n1 failed, 1 passed in 2.11s" },
        { type: "fileChange", id: "f1", status: "completed", changes: [{ path: "fetcher/client.py", kind: { type: "update", move_path: null }, diff: DIFF }] },
        { type: "agentMessage", id: "a1", text: "The loop swallowed the last `ConnectionError`. Now the last attempt re-raises, and the pause comes from `backoff()`, which the test patches to zero." },
      ],
    }]), status: { type: "active", activeFlags: [] } },
    // w1 is running (an approval waits, a subagent works), so the app resumes it to watch it; the others are read.
    thread("w2", fetcher, "Log every retry at debug level", "", ago(3), [{
      id: "t2", status: "inProgress", error: null, items: [
        { type: "userMessage", id: "u2", content: text("Log every retry at debug level, with the URL and the error.") },
        { type: "reasoning", id: "r2", summary: ["Finding where the retries happen."], content: [] },
        { type: "commandExecution", id: "c2", command: "rg -n \"retry\" fetcher/", cwd: fetcher, processId: null, status: "inProgress",
          commandActions: [], exitCode: null, durationMs: null, aggregatedOutput: "fetcher/client.py:12:    for attempt in range(attempts):\n" },
      ],
    }]),
    thread("w3", website, "Compress the hero image", "", ago(26 * 60)),
    { ...thread("s1", fetcher, null, "Read the retry tests", ago(10)), parentThreadId: "w1", agentNickname: "Ada", agentRole: "explorer", status: { type: "active", activeFlags: [] } },
    { ...thread("s2", fetcher, null, "Check the docs", ago(11)), parentThreadId: "w1", agentNickname: "Brin", agentRole: "reviewer" },
    thread("q1", path.join(askRoot, "q-1a2b3c"), "Why does the GB10 share memory with the GPU?", "", ago(40), [{
      id: "tq", status: "completed", error: null, items: [
        { type: "userMessage", id: "uq", content: text("Why does the GB10 share memory with the GPU?") },
        { type: "agentMessage", id: "aq", text: "Because the CPU and the GPU address **one pool** of LPDDR5X." },
      ],
    }]),
  ];
}

/** MIGHTLING_STAND_IN_IDLE=1 (DESKTOP §16 B43): nothing running, the question and a Work thread left two days ago. */
export function idleThreads(home: string, askRoot: string) {
  const { fetcher } = projectFolders(home);
  const question = threads(home, askRoot).find((entry) => entry.id === "q1");
  return [question, thread("w5", fetcher, "Speed up the CSV import", "Speed up the CSV import", Math.floor(Date.now() / 1000) - 2 * 24 * 3600, [{
    id: "t5", status: "completed", error: null, items: [
      { type: "userMessage", id: "u5", content: text("The CSV import takes a minute for 100k rows. Make it faster.") },
      { type: "agentMessage", id: "a5", text: "Rows are now read in batches of 1000; the import takes 6 seconds." },
    ],
  }])];
}

const FILES = ["fetcher/client.py", "fetcher/__init__.py", "fetcher/backoff.py", "tests/test_fetch.py", "README.md", "pyproject.toml"];

/** Writes the stand-in `ling` into `home` and returns its path. */
export function writeStandIn(home: string, askRoot: string): string {
  const ling = path.join(home, "ling");
  const { fetcher } = projectFolders(home);
  fs.writeFileSync(ling, `#!/usr/bin/env node
const fs = require("fs");
fs.appendFileSync(${JSON.stringify(path.join(home, "runs.jsonl"))}, JSON.stringify(process.argv.slice(2)) + "\\n");
// Airgapped on while the test has left a file saying so (links.e2e.ts).
if (process.argv[2] === "airgapped") { console.log('{"level":"' + (fs.existsSync(${JSON.stringify(path.join(home, "airgapped-on"))}) ? "on" : "off") + '","tier":"default","source":"default","invalid":[]}'); process.exit(0); }
if (process.argv[2] === "prompt") { process.stdout.write("THE ASK PROMPT"); process.exit(0); }
if (process.argv[2] === "docs") {
  // ling docs add|remove|reindex|status --json, over a file of collections (a project's knowledge, DESKTOP_PARITY_CLAUDE §4.7).
  const file = ${JSON.stringify(path.join(home, "docs-collections.json"))};
  const all = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : [];
  const counts = { documents: 1, indexed: 1, pending: 0, failed: 0, duplicates: 0, skipped: 0, chunks: 1, embedded: 1, needs_ocr_pages: 0, needs_ocr_documents: 0 };
  const verb = process.argv[3];
  if (verb === "add") {
    const name = process.argv[process.argv.indexOf("--name") + 1];
    if (all.some((c) => c.name === name)) { console.error("ling-docs: a collection named " + name + " already exists"); process.exit(1); }
    all.push({ name, root: process.argv[4], enabled: true, default: false, counts, last_status: "ok", last_run: 0, disk_bytes: 0 });
  } else if (verb === "remove") {
    all.splice(all.findIndex((c) => c.name === process.argv[4]), 1);
  } else if (verb === "status") {
    console.log(JSON.stringify({ collections: all, runtime_missing: null }));
    process.exit(0);
  }
  fs.writeFileSync(file, JSON.stringify(all));
  console.log("ok");
  process.exit(0);
}
if (process.argv[2] === "node") {
  // ling node list --json and ling node use <name> (Settings › Node): two nodes, the chosen one remembered in a file.
  const chosen = ${JSON.stringify(path.join(home, "node-choice"))};
  const used = fs.existsSync(chosen) ? fs.readFileSync(chosen, "utf8") : "spark-1";
  if (process.argv[3] === "use") { fs.writeFileSync(chosen, process.argv[4]); console.log("Using node " + process.argv[4] + " at http://192.168.50.106:8000/v1."); process.exit(0); }
  const node = (name, id, model) => ({ name, id, choice: name, inUse: used === name, model, contextTokens: model ? 262144 : null, state: model ? "ready" : "stopped", version: "1.7.1", coding: true });
  console.log(JSON.stringify({ thisMachine: "client", nodes: [node("spark-1", "11111111-aaaa", "RadixArk/Qwen3.8-27B-NVFP4"), node("spark-2", "22222222-bbbb", null)], rememberedAway: null, hint: null }));
  process.exit(0);
}
// ling-admin night every add|list|drop (MIGHTLING_ADMIN is this script too): templates kept in a file.
if (process.argv[2] === "night" && process.argv[3] === "every") {
  const kept = ${JSON.stringify(path.join(home, "repeating.json"))};
  const list = fs.existsSync(kept) ? JSON.parse(fs.readFileSync(kept, "utf8")) : [];
  if (process.argv[4] === "list") { for (const entry of list) console.log(JSON.stringify(entry)); process.exit(0); }
  if (process.argv[4] === "drop") { fs.writeFileSync(kept, JSON.stringify(list.filter((entry) => entry.id !== process.argv[5]))); console.log("Repeating task " + process.argv[5] + " dropped."); process.exit(0); }
  const args = process.argv.slice(5);
  const weekly = args[0] === "--weekly" ? args[1] : null;
  // DESKTOP §16 B42: on chosen weekdays, every N nights, on a day of the month, kept as the real template keeps them.
  const flag = ["--days", "--every", "--monthly"].find((name) => args[0] === name);
  const more = flag === "--days" ? { cadence: "days", days: args[1].split(",") } : flag === "--every" ? { cadence: "every", every: Number(args[1]) } : flag === "--monthly" ? { cadence: "monthly", day: Number(args[1]) } : {};
  const test = args.includes("--test") ? args[args.indexOf("--test") + 1] : null;
  const task = args.slice(args.indexOf("--") + 1).join(" ");
  const id = "r" + String(list.length + 1).padStart(12, "0");
  list.push({ id, repo: process.cwd(), task, test, cadence: weekly ? "weekly" : "daily", weekday: weekly, ...more, created: "2026-10-21T12:00:00+01:00", last_queued: null, last_error: null });
  fs.writeFileSync(kept, JSON.stringify(list));
  console.log("Repeating task " + id + ", " + (weekly ? "every " + weekly : flag ? flag.slice(2) + " " + args[1] : "every night") + ".");
  process.exit(0);
}
// ling night apply <id> (DESKTOP §16 B40): says where it ran, as the real one names the checkout.
if (process.argv[2] === "night" && process.argv[3] === "apply") { console.log("Applied night/" + process.argv[4] + " to " + process.cwd() + " as uncommitted changes."); process.exit(0); }
if (process.argv[2] === "night") { console.log(process.argv[3] === "add" ? "Queued 20261017-0930 on branch night/20261017-0930, from 1a2b3c4d5e." : "Dropped " + process.argv[4] + "."); process.exit(0); }
// MIGHTLING_STAND_IN_EMPTY=1: someone with no threads yet, as at the first start (DESKTOP §16 B6).
const threads = process.env.MIGHTLING_STAND_IN_EMPTY === "1" ? [] : ${JSON.stringify(threads(home, askRoot))};
if (process.env.MIGHTLING_STAND_IN_IDLE === "1") threads.splice(0, threads.length, ...${JSON.stringify(idleThreads(home, askRoot))});
const files = ${JSON.stringify(FILES)};
const fetcher = ${JSON.stringify(fetcher)};
const log = ${JSON.stringify(path.join(home, "received.jsonl"))};
const say = (message) => console.log(JSON.stringify(message));
const usage = { totalTokens: 41210, inputTokens: 39000, cachedInputTokens: 30000, cacheWriteInputTokens: 0, outputTokens: 2210, reasoningOutputTokens: 800 };
const queues = {};
const terminals = {};
const goals = {};
const watches = {};
const config = { features: { memories: false }, memories: {}, desktop: null };
// hooks.state as config/batchWrite leaves it: a hook is trusted once its trusted_hash is its current hash.
const hookState = {};
// Projects (item 13), as the server keeps them: roots in order, metadata replaced whole on update.
const projects = [];
const hookOf = (key, base) => {
  const state = hookState[key] || {};
  const trustStatus = base.isManaged ? "managed" : state.trusted_hash === base.currentHash ? "trusted" : state.trusted_hash ? "modified" : "untrusted";
  return { ...base, key, enabled: state.enabled !== false, trustStatus };
};
let asked = false;
// Ephemeral threads: never listed, still known by id (an incognito question's folder).
const unlisted = {};
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
    case "project/list": return answer({ data: projects, nextCursor: null });
    case "project/create": {
      const made = { id: "proj-" + (projects.length + 1), name: p.name, roots: p.roots || [], metadata: p.metadata || {}, position: projects.length, createdAt: 0, updatedAt: 0, recencyAt: null };
      projects.push(made);
      say({ method: "project/changed", params: { projectId: made.id, changeType: "created" } });
      return answer({ project: made });
    }
    case "project/update": {
      const found = projects.find((entry) => entry.id === p.projectId);
      if (!found) return say({ id: m.id, error: { code: -32602, message: "project not found: " + p.projectId } });
      if (p.name) found.name = p.name;
      if (p.roots) found.roots = p.roots;
      if (p.metadata) found.metadata = p.metadata;
      say({ method: "project/changed", params: { projectId: found.id, changeType: "updated" } });
      return answer({ project: found });
    }
    case "thread/metadata/update": {
      const found = threads.find((t) => t.id === p.threadId);
      if (!found) return say({ id: m.id, error: { code: -32600, message: "no such thread" } });
      if ("projectId" in p) found.projectId = p.projectId;
      return answer({ thread: { ...found, turns: [] } });
    }
    case "thread/list":
      if (p.archived) return answer({ data: [{ ...threads[0], id: "old1", name: "Try a smaller hero image", turns: [] }], nextCursor: null });
      // MIGHTLING_STAND_IN_PAGES=1 (DESKTOP §16 B10): a second page holds a thread of a project seen nowhere else.
      if (process.env.MIGHTLING_STAND_IN_PAGES === "1") {
        if (p.cursor === "page-2") return answer({ data: [{ ...threads[0], id: "old-quiet", cwd: require("node:path").join(${JSON.stringify(home)}, "quiet"), name: "Rename the old config keys", preview: "Rename the old config keys", updatedAt: 1000, createdAt: 1000, turns: [] }], nextCursor: null });
        return answer({ data: threads.map((t) => ({ ...t, turns: [] })), nextCursor: "page-2" });
      }
      return answer({ data: threads.map((t) => ({ ...t, turns: [] })), nextCursor: null });
    case "permissionProfile/list": return answer({ data: [":read-only", ":workspace", ":danger-full-access"].map((id) => ({ id, allowed: true })) });
    case "model/list": return answer({ data: [model], nextCursor: null });
    case "collaborationMode/list": return answer({ data: [{ name: "Default", mode: "default", model: null, reasoning_effort: null }, { name: "Plan", mode: "plan", model: null, reasoning_effort: null }] });
    case "skills/list": return answer({ data: (p.cwds || []).map((cwd) => ({ cwd, skills, errors: [] })) });
    case "fuzzyFileSearch": {
      const q = String(p.query || "").toLowerCase();
      // A folder with files in it is searched for real (Phase 3's search files); an empty one answers the made-up list.
      const walk = (dir, rel) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => d.name === ".git" ? [] : d.isDirectory() ? walk(dir + "/" + d.name, rel + d.name + "/") : [rel + d.name]);
      let real = [];
      try { real = walk(p.roots[0], ""); } catch {}
      const found = (real.length ? real : files).filter((f) => f.toLowerCase().includes(q)).map((f, i) => ({ root: p.roots[0], path: f, match_type: "file", file_name: f.split("/").pop(), score: 100 - i, indices: null }));
      return answer({ files: found });
    }
    case "thread/read": {
      const found = threads.find((t) => t.id === p.threadId);
      if (!found) return say({ id: m.id, error: { code: -32600, message: "no such thread" } });
      return answer({ thread: found });
    }
    case "thread/resume": {
      const found = threads.find((t) => t.id === p.threadId);
      if (!found) return say({ id: m.id, error: { code: -32600, message: "no such thread" } });
      answer({ thread: found });
      if (found && found.id === "w1") {
        say({ method: "turn/started", params: { threadId: "s1", turn: { id: "ts1", status: "inProgress", items: [], error: null } } });
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
    case "thread/goal/get": return answer({ goal: goals[p.threadId] || null });
    case "thread/goal/set": {
      const now = Math.floor(Date.now() / 1000);
      const before = goals[p.threadId] || { threadId: p.threadId, objective: "", status: "active", tokenBudget: null, tokensUsed: 0, timeUsedSeconds: 0, createdAt: now, updatedAt: now };
      const goal = { ...before, ...(p.objective ? { objective: p.objective } : {}), ...(p.status ? { status: p.status } : {}), tokensUsed: before.tokensUsed + 1800, timeUsedSeconds: before.timeUsedSeconds + 95, updatedAt: now };
      goals[p.threadId] = goal;
      answer({ goal });
      return say({ method: "thread/goal/updated", params: { threadId: p.threadId, turnId: null, goal } });
    }
    case "thread/goal/clear":
      delete goals[p.threadId];
      answer({ cleared: true });
      return say({ method: "thread/goal/cleared", params: { threadId: p.threadId } });
    case "thread/archive": return answer({});
    case "thread/delete": {
      // Deleted for good (DESKTOP §16 B36): the thread and the subagents' threads it started, each said as deleted.
      const gone = threads.filter((t) => t.id === p.threadId || t.parentThreadId === p.threadId).map((t) => t.id);
      if (!gone.length) return say({ id: m.id, error: { code: -32600, message: "thread not found: " + p.threadId } });
      for (const id of gone) threads.splice(threads.findIndex((t) => t.id === id), 1);
      answer({});
      return gone.forEach((id) => say({ method: "thread/deleted", params: { threadId: id } }));
    }
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
    case "thread/queue/update": {
      const item = (queues[p.threadId] || []).find((q) => q.id === p.queuedSubmissionId);
      if (item) item.input = p.input;
      answer({ queuedSubmission: item || null });
      return say({ method: "thread/queue/changed", params: { threadId: p.threadId } });
    }
    case "thread/queue/reorder": {
      const byId = Object.fromEntries((queues[p.threadId] || []).map((q) => [q.id, q]));
      queues[p.threadId] = p.queuedSubmissionIds.map((id) => byId[id]).filter(Boolean);
      answer({});
      return say({ method: "thread/queue/changed", params: { threadId: p.threadId } });
    }
    case "thread/queue/delete":
      queues[p.threadId] = (queues[p.threadId] || []).filter((q) => q.id !== p.queuedSubmissionId);
      answer({});
      return say({ method: "thread/queue/changed", params: { threadId: p.threadId } });
    case "thread/fork": {
      const source = threads.find((t) => t.id === p.threadId);
      // Through a turn (lastTurnId) or before one (beforeTurnId): the turns cut there.
      const turns = source.turns || [];
      const at = turns.findIndex((t) => t.id === (p.lastTurnId || p.beforeTurnId));
      const cut = at < 0 ? turns : p.lastTurnId ? turns.slice(0, at + 1) : turns.slice(0, at);
      const copy = { ...source, turns: cut, id: source.id + "-fork" + (p.lastTurnId || p.beforeTurnId ? "-" + next++ : ""), name: (source.name || "") + " (fork)", updatedAt: Math.floor(Date.now() / 1000) };
      // A fork into another folder (DESKTOP §16 B33: an Ask question continued in a project) works there.
      if (p.cwd) copy.cwd = p.cwd;
      threads.push(copy);
      return answer({ thread: copy, model: "qwen3.8-27b", modelProvider: "local", cwd: copy.cwd });
    }
    case "thread/start": {
      // An ephemeral thread in an Ask folder is an incognito question ("i"), answered as a question is;
      // any other ephemeral one is a helper's ("e": a commit message, suggestions).
      const id = (p.ephemeral ? (String(p.cwd || "").startsWith(${JSON.stringify(askRoot)}) ? "i" : "e") : "n") + next++;
      const started = { id, cwd: p.cwd, name: null, preview: "", turns: [], status: { type: "idle" }, ephemeral: !!p.ephemeral, parentThreadId: null,
        createdAt: Math.floor(Date.now() / 1000), updatedAt: Math.floor(Date.now() / 1000), recencyAt: null, model: "qwen3.8-27b", modelProvider: "local",
        projectId: p.projectId || null, runtimeWorkspaceRoots: p.runtimeWorkspaceRoots || [] };
      if (!p.ephemeral) threads.push(started);
      else unlisted[id] = started;
      return answer({ thread: started, model: "qwen3.8-27b", modelProvider: "local", cwd: p.cwd });
    }
    case "thread/shellCommand": {
      // A ! command, as Codex runs one: its own turn, a commandExecution item from the user's shell.
      const id = "turn-" + next++;
      const own = threads.find((t) => t.id === p.threadId) || unlisted[p.threadId];
      const item = { type: "commandExecution", id: "sh" + id, command: p.command, cwd: (own && own.cwd) || fetcher, processId: null, source: "userShell",
        status: "inProgress", commandActions: [], exitCode: null, durationMs: null, aggregatedOutput: null };
      answer({});
      say({ method: "turn/started", params: { threadId: p.threadId, turn: { id, items: [], status: "inProgress", error: null } } });
      say({ method: "item/started", params: { threadId: p.threadId, turnId: id, item } });
      say({ method: "item/commandExecution/outputDelta", params: { threadId: p.threadId, turnId: id, itemId: item.id, delta: "On branch main\\n" } });
      say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { ...item, status: "completed", exitCode: 0, durationMs: 12, aggregatedOutput: "On branch main\\nnothing to commit, working tree clean\\n" } } });
      return say({ method: "turn/completed", params: { threadId: p.threadId, turn: { id, items: [], status: "completed", error: null } } });
    }
    case "turn/start": {
      const id = "turn-" + next++;
      if (/^[ni]/.test(String(p.threadId))) {
        // A thread the test started: the turn runs briefly and answers.
        answer({ turn: { id, items: [], status: "inProgress", error: null } });
        say({ method: "turn/started", params: { threadId: p.threadId, turn: { id, items: [], status: "inProgress", error: null } } });
        // The user's message as an item, as the server reports it.
        say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { type: "userMessage", id: "u" + id, content: p.input || [] } } });
        const asked = String(((p.input || [])[0] || {}).text || "");
        // The agent's plan as a checklist (DESKTOP §16 B3): one update at once, the next a moment later, then the answer.
        const planned = asked.includes("with a plan");
        if (planned) {
          say({ method: "turn/plan/updated", params: { threadId: p.threadId, turnId: id, explanation: null, plan: [{ step: "Read the retry loop", status: "inProgress" }, { step: "Re-raise the last error", status: "pending" }, { step: "Run the tests", status: "pending" }] } });
          setTimeout(() => say({ method: "turn/plan/updated", params: { threadId: p.threadId, turnId: id, explanation: "The loop was simpler than it looked.", plan: [{ step: "Read the retry loop", status: "completed" }, { step: "Re-raise the last error", status: "completed" }, { step: "Run the tests", status: "inProgress" }] } }), 1500);
        }
        return setTimeout(() => {
          // An MCP server asking (item 14): a form, or a link to open.
          if (asked.includes("with a form")) {
            say({ id: "elicit-1", method: "mcpServer/elicitation/request", params: { threadId: p.threadId, turnId: id, serverName: "tickets", mode: "form", _meta: null,
              message: "Which ticket should this go under?", requestedSchema: { type: "object", required: ["ticket"], properties: {
                ticket: { type: "string", title: "Ticket", description: "Its key, such as OPS-12" },
                urgent: { type: "boolean", title: "Urgent" },
                team: { type: "string", title: "Team", oneOf: [{ const: "web", title: "Web" }, { const: "infra", title: "Infrastructure" }] } } } } });
          }
          if (asked.includes("with a link")) {
            say({ id: "elicit-2", method: "mcpServer/elicitation/request", params: { threadId: p.threadId, turnId: id, serverName: "tickets", mode: "url", _meta: null,
              message: "Sign in to the ticket tracker.", url: "http://127.0.0.1:9/login", elicitationId: "e2" } });
          }
          // A question from the agent (DESKTOP §16 B2): options with descriptions and a free answer, and a secret.
          if (asked.includes("with a question")) {
            say({ id: "input-1", method: "item/tool/requestUserInput", params: { threadId: p.threadId, turnId: id, itemId: "q" + id, isBlocking: true, autoResolutionMs: null, questions: [
              { id: "scope", header: "Scope", question: "Which part first?", isOther: true, isSecret: false, options: [
                { label: "The client", description: "fetcher/client.py and its retry loop" },
                { label: "The tests", description: "tests/test_client.py" }] },
              { id: "token", header: "Token", question: "The staging token?", isOther: false, isSecret: true, options: null }] } });
          }
          // A command to approve with a rule proposed for later (DESKTOP §16 B8).
          if (asked.includes("with a rule")) {
            say({ id: "approve-rule", method: "item/commandExecution/requestApproval", params: { threadId: p.threadId, turnId: id, itemId: "c" + id, command: "cargo test -p fetcher",
              proposedExecpolicyAmendment: ["cargo", "test"], proposedNetworkPolicyAmendments: [{ host: "crates.io", action: "allow" }] } });
          }
          // An MCP tool call with its arguments, progress and result (DESKTOP §16 B11).
          if (asked.includes("with a tool")) {
            const call = { type: "mcpToolCall", id: "t" + id, server: "tickets", tool: "create_ticket", status: "inProgress", arguments: { title: "Retry swallows errors", team: "web" }, appContext: null, mcpAppUi: null, pluginId: null, readOnlyHint: null, result: null, error: null, durationMs: null };
            say({ method: "item/started", params: { threadId: p.threadId, turnId: id, item: call } });
            say({ method: "item/mcpToolCall/progress", params: { threadId: p.threadId, turnId: id, itemId: call.id, message: "Filing under web" } });
            say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { ...call, status: "completed", durationMs: 1200, result: { content: [{ type: "text", text: "Created OPS-12" }], structuredContent: null, _meta: null } } } });
          }
          if (asked.includes("with a report")) {
            // An answer that makes a file in its folder (DESKTOP_PARITY_CLAUDE §4.1): written, then reported as added.
            const own = threads.find((t) => t.id === p.threadId) || unlisted[p.threadId];
            const report = require("node:path").join((own && own.cwd) || "/tmp", "report.md");
            require("node:fs").writeFileSync(report, "# Memory on the GB10\\n\\nOne pool of LPDDR5X, shared by the CPU and the GPU.\\n");
            say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { type: "fileChange", id: "f" + id, status: "completed",
              changes: [{ path: report, kind: { type: "add" }, diff: "+# Memory on the GB10\\n" }] } } });
          }
          // /init (DESKTOP §16 B38): AGENTS.md at the folder's root, added, or edited when one is there.
          if (asked.includes("AGENTS.md for this repository") || asked.startsWith("This repository already has an AGENTS.md")) {
            const own = threads.find((t) => t.id === p.threadId) || unlisted[p.threadId];
            const agents = require("node:path").join((own && own.cwd) || "/tmp", "AGENTS.md");
            const had = require("node:fs").existsSync(agents);
            require("node:fs").appendFileSync(agents, "# fetcher\\n\\nTests: pytest -q\\n");
            say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { type: "fileChange", id: "f" + id, status: "completed",
              changes: [{ path: agents, kind: had ? { type: "update", move_path: null } : { type: "add" }, diff: "+# fetcher\\n+\\n+Tests: pytest -q\\n" }] } } });
          }
          // The window's own tools (the owner's decision 5): the agent reads the terminal and the preview's console.
          if (asked.includes("with the terminal")) {
            say({ id: "tool-1", method: "item/tool/call", params: { threadId: p.threadId, turnId: id, callId: "call-1", namespace: null, tool: "mightling_terminal_output", arguments: { lines: 5 } } });
            say({ id: "tool-2", method: "item/tool/call", params: { threadId: p.threadId, turnId: id, callId: "call-2", namespace: null, tool: "mightling_preview_console", arguments: {} } });
          }
          if (asked.includes("with a command")) {
            say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { type: "commandExecution", id: "c" + id, command: "pytest -q", cwd: fetcher, processId: null, status: "completed", commandActions: [], exitCode: 0, durationMs: 900, aggregatedOutput: "3 passed in 0.9s" } } });
          }
          const answerItem = { type: "agentMessage", id: "m" + id, text: asked.includes("with a diagram")
            ? "The retry, drawn:\\n\\n\u0060\u0060\u0060mermaid\\nflowchart LR\\n  A[Request] --> B{Failed?}\\n  B -- yes --> C[Wait, then retry]\\n  B -- no --> D[Answer]\\n\u0060\u0060\u0060\\n"
            : asked.includes("with a source")
            ? "NVFP4 is described on [NVIDIA's developer blog](https://developer.nvidia.com/blog/nvfp4/) [1].\\n\\n[1] https://developer.nvidia.com/blog/nvfp4/"
            : "Done: the retry counter is in fetcher/client.py." };
          say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: answerItem } });
          // Kept, as ling keeps it, so a thread opened again (or forked) has its turns.
          const kept = threads.find((t) => t.id === p.threadId);
          if (kept) kept.turns = [...(kept.turns || []), { id, status: "completed", error: null, items: [{ type: "userMessage", id: "u" + id, content: p.input || [] }, answerItem] }];
          // As ling sends it: the completed turn lists the answer alone, not what the turn streamed.
          say({ method: "turn/completed", params: { threadId: p.threadId, turn: { id, items: [answerItem], status: "completed", error: null } } });
        }, planned ? 4000 : 300);
      }
      // A side chat (DESKTOP §16 B37): unkept and read-only, known by the recap its first message carries; its turns stream.
      const sideWords = String(((p.input || [])[0] || {}).text || "");
      if (String(p.threadId).startsWith("e") && unlisted[p.threadId] && (unlisted[p.threadId].side || sideWords.includes("Context: this was asked in the conversation"))) {
        unlisted[p.threadId].side = true;
        answer({ turn: { id, items: [], status: "inProgress", error: null } });
        say({ method: "turn/started", params: { threadId: p.threadId, turn: { id, items: [], status: "inProgress", error: null } } });
        say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { type: "userMessage", id: "u" + id, content: p.input || [] } } });
        return setTimeout(() => {
          const sideAnswer = { type: "agentMessage", id: "m" + id, text: sideWords.includes("Context:") ? "Left: the docs. The retry fix is in." : "Yes, only the docs." };
          say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: sideAnswer } });
          say({ method: "turn/completed", params: { threadId: p.threadId, turn: { id, items: [sideAnswer], status: "completed", error: null } } });
        }, 300);
      }
      // A finished chat's summary (DESKTOP §16 B43): two lines, in a read-only, unkept thread.
      if (String(p.threadId).startsWith("e") && sideWords.startsWith("Below is a conversation between")) {
        answer({ turn: { id, items: [], status: "inProgress", error: null } });
        const summary = { type: "agentMessage", id: "m" + id, text: "- Wanted the CSV import made faster\\n- Done: rows read in batches of 1000" };
        say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: summary } });
        return say({ method: "turn/completed", params: { threadId: p.threadId, turn: { id, items: [summary], status: "completed", error: null } } });
      }
      if (String(p.threadId).startsWith("e")) {
        answer({ turn: { id, items: [], status: "inProgress", error: null } });
        // A read-only, unkept thread: a commit message, or (Phase 3's suggested prompts) three follow-ups.
        const asked = String(((p.input || [])[0] || {}).text || "");
        const reply = asked.startsWith("Below are the last request") ? "1. Add a test for the retry limit\\n2. Log the URL on every retry\\n3. Run the whole suite" : ${JSON.stringify(COMMIT_MESSAGE)};
        say({ method: "item/completed", params: { threadId: p.threadId, turnId: id, item: { type: "agentMessage", id: "m" + id, text: reply } } });
        return say({ method: "turn/completed", params: { threadId: p.threadId, turn: { id, items: [], status: "completed", error: null } } });
      }
      answer({ turn: { id, items: [], status: "inProgress", error: null } });
      return say({ method: "turn/started", params: { threadId: p.threadId, turn: { id, items: [], status: "inProgress", error: null } } });
    }
    case "turn/steer": return answer({ turnId: p.expectedTurnId });
    case "app/list": return answer({ data: [{ id: "gmail", name: "Gmail", description: "Search and read your mail", logoUrl: null, logoUrlDark: null, iconAssets: null, iconDarkAssets: null,
      distributionChannel: null, branding: null, appMetadata: null, labels: null, installUrl: null, isAccessible: true, isEnabled: true, pluginDisplayNames: [] }], nextCursor: null });
    case "memory/status": return answer({ v2ConsolidatedThreads: 7, v2Ready: true });
    case "memory/reset": return answer({});
    case "thread/memoryMode/set": return answer({});
    case "config/read": return answer({ config, origins: {}, layers: null });
    case "plugin/list": return answer({ marketplaces: [{ name: "Team tools", path: "/srv/team/marketplace.json", interface: null, plugins: [
      { id: "lint@team", remotePluginId: null, version: "1.2.0", localVersion: null, name: "lint", shareContext: null, source: { type: "local", path: "/srv/team/lint" }, installed: false, installedAt: null, enabled: false,
        installPolicy: "AVAILABLE", installPolicySource: null, mustShowInstallationInterstitial: null, authPolicy: "ON_INSTALL", availability: "AVAILABLE", disabledReason: null, eligiblePlanTypes: null, interface: null, keywords: [] },
      { id: "format@team", remotePluginId: null, version: "0.3.0", localVersion: null, name: "format", shareContext: null, source: { type: "local", path: "/srv/team/format" }, installed: true, installedAt: null, enabled: true,
        installPolicy: "AVAILABLE", installPolicySource: null, mustShowInstallationInterstitial: null, authPolicy: "ON_INSTALL", availability: "AVAILABLE", disabledReason: null, eligiblePlanTypes: null, interface: null, keywords: ["style"] }] }],
      marketplaceLoadErrors: [], featuredPluginIds: [] });
    case "plugin/read": return answer({ plugin: { marketplaceName: "Team tools", marketplacePath: p.marketplacePath, shareUrl: null, description: "Runs the team's checks before a commit.", onboardingSkill: null, appTemplates: [], scheduledTasks: null,
      summary: { id: p.pluginName + "@team", name: p.pluginName, version: "1.2.0", localVersion: null, keywords: [] }, skills: [{ name: "lint-check", description: "Lints the changed files", shortDescription: null, interface: null, path: null, enabled: true }],
      hooks: [{ key: "lint:postToolUse:0", eventName: "postToolUse" }], apps: [], mcpServers: ["lint-server"] } });
    case "marketplace/remove": return answer({ marketplaceName: p.marketplaceName, installedRoot: null });
    case "plugin/install": return answer({ authPolicy: "ON_INSTALL", appsNeedingAuth: [] });
    case "plugin/uninstall": return answer({});
    case "skills/config/write": return answer({ effectiveEnabled: p.enabled });
    case "config/mcpServer/reload":
      // A reload restarts the servers: one reports how its start went (DESKTOP §16 B34).
      say({ method: "mcpServer/startupStatus/updated", params: { threadId: null, name: "ling-code", status: "failed", error: "exited with status 1", failureReason: null } });
      return answer({});
    case "hooks/list": return answer({ data: [{ cwd: (p.cwds || ["/"])[0], warnings: [], errors: [], hooks: [
      { key: "ledger", eventName: "Stop", matcher: null, timeoutSec: 30, statusMessage: null, additionalContextLimit: null, sourcePath: "/home/u/.mightling/hooks.json", source: "user",
        pluginId: null, displayOrder: 0, isManaged: true, currentHash: "x", handlerType: "command", command: "ling ledger", async: false },
      { key: "/w/app/.codex/hooks.json:post_tool_use:0:0", eventName: "PostToolUse", matcher: "Bash", timeoutSec: 30, statusMessage: null, additionalContextLimit: null,
        sourcePath: "/w/app/.codex/hooks.json", source: "project", pluginId: null, displayOrder: 1, isManaged: false, currentHash: "sha256:audit-v1", handlerType: "command",
        command: "./scripts/audit.sh", async: false },
    ].map((hook) => hookOf(hook.key, hook)) }] });
    case "config/batchWrite": {
      for (const edit of p.edits || []) {
        if (String(edit.keyPath).startsWith("mcp_servers.") && edit.value === null && config.mcp_servers) delete config.mcp_servers[edit.keyPath.slice(12)];
        if (edit.keyPath !== "hooks.state") continue;
        for (const [key, value] of Object.entries(edit.value || {})) hookState[key] = { ...(hookState[key] || {}), ...value };
      }
      return answer({ status: "ok", version: "1", filePath: "/x", overriddenMetadata: null });
    }
    case "config/value/write": {
      // Kept, so a later config/read answers what was written.
      const keys = String(p.keyPath).split(".");
      let at = config;
      for (const key of keys.slice(0, -1)) at = at[key] = (at[key] && typeof at[key] === "object" ? at[key] : {});
      at[keys[keys.length - 1]] = p.value;
      return answer({ status: "ok", version: "1", filePath: "/x", overriddenMetadata: null });
    }
    case "review/start": {
      const id = "turn-" + next++;
      const where = p.delivery === "detached" ? "r" + next++ : p.threadId;
      answer({ turn: { id, items: [], status: "inProgress", error: null }, reviewThreadId: where });
      say({ method: "turn/started", params: { threadId: where, turn: { id, items: [], status: "inProgress", error: null } } });
      say({ method: "item/completed", params: { threadId: where, turnId: id, item: { type: "enteredReviewMode", id: "rv1", review: "uncommitted changes" } } });
      // Asked for findings (DESKTOP §16 B12): the block as ling formats it, one on a line of the turn's diff and one elsewhere.
      const withFindings = p.target && p.target.type === "custom" && /findings/.test(String(p.target.instructions || ""));
      const at = require("node:path").join(${JSON.stringify(projectFolders(home).fetcher)}, "fetcher");
      const review = withFindings
        ? "Two things to fix.\\n\\nFull review comments:\\n\\n- Timeout is hard-coded — " + at + "/client.py:14-14\\n  Pass the timeout in from the caller.\\n\\n- Backoff has no cap — " + at + "/backoff.py:3-5\\n  The delay doubles without a ceiling."
        : "**One finding.** fetcher/client.py:15 re-raises without the attempt count in the message.";
      say({ method: "item/completed", params: { threadId: where, turnId: id, item: { type: "exitedReviewMode", id: "rv2", review } } });
      return say({ method: "turn/completed", params: { threadId: where, turn: { id, items: [], status: "completed", error: null } } });
    }
    // A stand-in PTY: a fake shell that echoes what is typed and runs echo, enough to show the
    // window's plumbing (command/exec, write, resize, terminate, outputDelta).
    case "command/exec": {
      const id = p.processId;
      const out = (text) => say({ method: "command/exec/outputDelta", params: { processId: id, stream: "stdout", deltaBase64: Buffer.from(text).toString("base64"), capReached: false } });
      // [sh, -c, exec $SHELL -l -c "$0", script] or [shell, -l, -c, script]: a script; anything shorter is a shell.
      const script = p.command.length === 4 ? p.command[3] : null;
      if (script) {
        out("$ " + script + "\\r\\n" + (String(script).startsWith("echo ") ? String(script).slice(5) + "\\r\\n" : ""));
        return setTimeout(() => answer({ exitCode: 0, stdout: "", stderr: "" }), 50);
      }
      terminals[id] = { line: "", answer };
      return out("stand-in shell in " + p.cwd + "\\r\\n$ ");
    }
    case "command/exec/write": {
      const term = terminals[p.processId];
      const out = (text) => say({ method: "command/exec/outputDelta", params: { processId: p.processId, stream: "stdout", deltaBase64: Buffer.from(text).toString("base64"), capReached: false } });
      for (const ch of Buffer.from(p.deltaBase64, "base64").toString()) {
        if (ch === "\\r") {
          const line = term.line;
          term.line = "";
          out("\\r\\n" + (line.startsWith("echo ") ? line.slice(5) + "\\r\\n" : line ? line.split(" ")[0] + ": not in the stand-in\\r\\n" : "") + "$ ");
        } else {
          term.line += ch;
          out(ch);
        }
      }
      return answer({});
    }
    case "command/exec/resize": return answer({});
    case "command/exec/terminate": {
      const term = terminals[p.processId];
      answer({});
      if (term) term.answer({ exitCode: 130, stdout: "", stderr: "" });
      delete terminals[p.processId];
      return;
    }
    case "externalAgentConfig/detect":
      return answer({ connectors: [], items: [
        { itemType: "AGENTS_MD", description: "Instructions in " + fetcher + "/OTHER-AGENT.md, as AGENTS.md", cwd: fetcher, details: null },
        { itemType: "SKILLS", description: "2 skills in ~/.other-agent/skills", cwd: null, details: null },
        { itemType: "PLUGINS", description: "1 plugin from a marketplace", cwd: null, details: null },
        { itemType: "SESSIONS", description: "14 past sessions", cwd: null, details: null },
      ] });
    case "externalAgentConfig/import": {
      const importId = "imp-" + next++;
      answer({ importId });
      const results = (p.migrationItems || []).map((item) => ({ itemType: item.itemType, successes: [{ itemType: item.itemType, cwd: item.cwd, source: null, target: null, title: null }], failures: [] }));
      say({ method: "externalAgentConfig/import/progress", params: { importId, itemTypeResults: results.slice(0, 1) } });
      return setTimeout(() => say({ method: "externalAgentConfig/import/completed", params: { importId, itemTypeResults: results } }), 200);
    }
    case "externalAgentConfig/import/readHistories":
      return answer({ connectors: [], data: [{ importId: "imp-0", providerId: "mightling_desktop", completedAtMs: Date.now() - 86400000, successes: [{}], failures: [] }] });
    case "thread/turns/list": {
      const found = threads.find((t) => t.id === p.threadId);
      const turns = found ? [...(found.turns || [])] : [];
      return answer({ data: p.sortDirection === "asc" ? turns : turns.reverse(), nextCursor: null, backwardsCursor: null });
    }
    case "fs/readDirectory":
      try {
        return answer({ entries: fs.readdirSync(p.path, { withFileTypes: true }).map((d) => ({ fileName: d.name, isDirectory: d.isDirectory(), isFile: d.isFile() })) });
      } catch (error) {
        return say({ id: m.id, error: { code: -32600, message: String(error.message) } });
      }
    case "fs/readFile":
      try {
        return answer({ dataBase64: fs.readFileSync(p.path).toString("base64") });
      } catch (error) {
        return say({ id: m.id, error: { code: -32600, message: String(error.message) } });
      }
    case "fs/getMetadata": {
      const stat = fs.statSync(p.path, { throwIfNoEntry: false });
      if (!stat) return say({ id: m.id, error: { code: -32600, message: "no such file" } });
      return answer({ isDirectory: stat.isDirectory(), isFile: stat.isFile(), isSymlink: false, createdAtMs: 0, modifiedAtMs: Math.floor(stat.mtimeMs) });
    }
    case "fs/watch":
      try {
        watches[p.watchId] = fs.watch(p.path, () => say({ method: "fs/changed", params: { watchId: p.watchId, changedPaths: [p.path] } }));
      } catch {}
      return answer({ path: p.path });
    case "fs/unwatch":
      if (watches[p.watchId]) watches[p.watchId].close();
      delete watches[p.watchId];
      return answer({});
    case "mcpServerStatus/list": return answer({ data: [{ name: "ling-code", tools: { def: {}, refs: {}, callers: {} }, toolsError: null },
      ...Object.keys(config.mcp_servers || {}).map((name) => ({ name, tools: {}, toolsError: null, runtimeStatus: null, pluginId: null }))], nextCursor: null });
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
