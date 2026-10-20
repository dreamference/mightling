// The composer (DESKTOP_PARITY §2.3) against a scripted app-server stand-in, one scenario per row
// shipped: `@` files, `$` skills, `/` commands, what Enter does, the follow-up (steer or queue, and
// a queued one started by the server), ↑ restore, Shift+drag images by path, Enter and Esc on an
// approval, Esc Esc to edit the previous message, fork, and the model and effort picker. No window.
import { describe, expect, it } from "vitest";

import {
  PromptHistory, reviewTarget, activeTrigger, collaborationMode, applyCompletion, composeInput, effortChoices, effortFor, enterSends, keyAnswer,
  lastUserTurn, matchCommands, matchSkills, mentionPath, parseSlash, rememberModel, userTexts,
} from "./composer";
import { DEFAULT_PREFS, cleanAppearance, sanitize } from "./prefs";
import { ScriptedServer } from "./scripted-server";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { Model } from "./protocol/v2/Model";
import type { SkillMetadata } from "./protocol/v2/SkillMetadata";
import type { Thread } from "./protocol/v2/Thread";
import { initialState, reduce, type ThreadView } from "./store";
import { goalTime } from "./GoalRow";
import { forkThread, listQueue, sendMessage, steerQueued, takeBack } from "./turns";

const text = (value: string) => ({ type: "text" as const, text: value, text_elements: [] });

function threadView(turns: ThreadView["turns"], activeTurnId: string | null = null): ThreadView {
  return { thread: { id: "t", cwd: "/w/proj", turns: [] } as unknown as Thread, turns, activeTurnId, usage: null };
}

const skill = (name: string, enabled = true): SkillMetadata =>
  ({ name, description: `${name} skill`, path: `/skills/${name}/SKILL.md`, scope: "user", enabled, pluginId: null } as unknown as SkillMetadata);

const model = { id: "qwen3.8-27b", model: "qwen3.8-27b", displayName: "Qwen3.8 27B", supportedReasoningEfforts: [{ reasoningEffort: "low", description: "" }, { reasoningEffort: "medium", description: "" }, { reasoningEffort: "high", description: "" }], defaultReasoningEffort: "medium" } as unknown as Model;

describe("the composer's menus", () => {
  it("@ completes a file from fuzzyFileSearch into a path relative to the thread's folder", () => {
    const draft = "look at @appts";
    const trigger = activeTrigger(draft, draft.length);
    expect(trigger).toEqual({ kind: "@", query: "appts", start: 8, end: 14 });
    const path = mentionPath("/w/proj", "/w/proj/src/App.tsx");
    expect(path).toBe("src/App.tsx");
    expect(applyCompletion(draft, trigger!, `@${path}`)).toEqual({ text: "look at @src/App.tsx ", caret: 21 });
    expect(mentionPath("/w/proj", "/elsewhere/a b.txt")).toBe('"/elsewhere/a b.txt"');
  });

  it("$ lists skills and sends a chosen one as a skill item beside the text", () => {
    const skills = [skill("pdf"), skill("deploy"), skill("old", false)];
    expect(matchSkills(skills, "de").map((s) => s.name)).toEqual(["deploy"]);
    expect(activeTrigger("use $pd", 7)).toMatchObject({ kind: "$", query: "pd" });
    expect(composeInput("use $pdf and $old and $nothing", skills)).toEqual([
      text("use $pdf and $old and $nothing"),
      { type: "skill", name: "pdf", path: "/skills/pdf/SKILL.md" },
    ]);
  });

  it("/ opens the commands only at the start of a message, and only the ones this view can run", () => {
    expect(activeTrigger("/fo", 3)).toMatchObject({ kind: "/", query: "fo" });
    expect(activeTrigger("see /tmp/x", 10)).toBeNull();
    expect(matchCommands("", { view: "ask", hasThread: false }).map((c) => c.name)).not.toContain("fork");
    expect(matchCommands("", { view: "ask", hasThread: false }).map((c) => c.name)).not.toContain("permissions");
    expect(matchCommands("f", { view: "work", hasThread: true }).map((c) => c.name)[0]).toBe("fork");
    expect(parseSlash("/rename  Retry fix ")).toMatchObject({ command: { name: "rename" }, args: "Retry fix" });
    expect(parseSlash("/airgapped default on")).toMatchObject({ command: { name: "airgapped" }, args: "default on" });
    expect(parseSlash("/cavemode default full")).toMatchObject({ command: { name: "cavemode" }, args: "default full" });
    expect(parseSlash("/nonesuch")).toBeNull();
    expect(parseSlash("an ordinary /message")).toBeNull();
  });
});

describe("Enter, the history and the approval keys", () => {
  const press = (key: string, mods: Partial<{ shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; altKey: boolean }> = {}) =>
    ({ key, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...mods });

  it("Enter sends or makes a new line, as the window's preference says", () => {
    expect(enterSends(press("Enter"), "enter")).toBe(true);
    expect(enterSends(press("Enter", { shiftKey: true }), "enter")).toBe(false);
    expect(enterSends(press("Enter"), "ctrlEnter")).toBe(false);
    expect(enterSends(press("Enter", { ctrlKey: true }), "ctrlEnter")).toBe(true);
    expect(DEFAULT_PREFS.enterBehaviour).toBe("enter");
    expect(sanitize({ enterBehaviour: "ctrlEnter", followUp: "sometimes", ghost: 1 })).toEqual({ ...DEFAULT_PREFS, enterBehaviour: "ctrlEnter" });
  });

  it("↑ in an empty composer walks back through earlier prompts, ↓ comes back to the draft", () => {
    const history = new PromptHistory();
    history.push("first");
    history.push("second");
    const thread = ["from the TUI", "first"];
    expect(history.older("", thread)).toBe("second");
    expect(history.older("", thread)).toBe("first");
    expect(history.older("", thread)).toBe("from the TUI");
    expect(history.older("", thread)).toBeNull();
    expect(history.newer(thread)).toBe("first");
    expect(history.newer(thread)).toBe("second");
    expect(history.newer(thread)).toBe("");
    expect(history.walking).toBe(false);
  });

  it("Enter approves and Esc declines an open approval, by its kind", () => {
    expect(keyAnswer("item/commandExecution/requestApproval", "approve")).toEqual({ decision: "accept" });
    expect(keyAnswer("item/fileChange/requestApproval", "decline")).toEqual({ decision: "decline" });
    expect(keyAnswer("execCommandApproval", "decline")).toEqual({ decision: { denied: { rejection: "declined in the app" } } });
    expect(keyAnswer("item/permissions/requestApproval", "approve", { permissions: { network: { enabled: true }, fileSystem: null } }))
      .toEqual({ permissions: { network: { enabled: true } }, scope: "turn" });
    expect(keyAnswer("item/tool/requestUserInput", "approve")).toBeNull();
  });

  it("Shift+drag images are sent by their path, after the text", () => {
    expect(composeInput("what is wrong here?", [], ["/home/u/shot.png"])).toEqual([
      text("what is wrong here?"),
      { type: "localImage", path: "/home/u/shot.png" },
    ]);
    expect(composeInput("", [], ["/home/u/shot.png"])).toEqual([{ type: "localImage", path: "/home/u/shot.png" }]);
  });
});

describe("follow-ups against the scripted server", () => {
  it("a message sent while a turn runs steers it, by default", async () => {
    const server = new ScriptedServer({ "turn/steer": () => ({ turnId: "turn-1" }) });
    const sent = await sendMessage(server.client, { threadId: "t", activeTurnId: "turn-1", input: [text("also the docs")], followUp: "steer" });
    expect(sent).toBe("steered");
    expect(server.sent).toEqual([{ method: "turn/steer", params: { threadId: "t", input: [text("also the docs")], expectedTurnId: "turn-1" } }]);
  });

  it("with the queue preference it waits, and the server starts it when the turn ends", async () => {
    const queue: { id: string; input: unknown[]; clientUserMessageId: string }[] = [];
    const server = new ScriptedServer({
      "thread/queue/add": (params) => {
        const item = { id: `q${queue.length + 1}`, input: params.input as unknown[], clientUserMessageId: params.clientUserMessageId as string };
        queue.push(item);
        return { queuedSubmission: item };
      },
      "thread/queue/list": () => ({ data: [...queue], nextCursor: null }),
    });
    const sent = await sendMessage(server.client, { threadId: "t", activeTurnId: "turn-1", input: [text("then run the tests")], followUp: "queue" });
    expect(sent).toBe("queued");
    expect(server.sent[0].method).toBe("thread/queue/add");
    expect(typeof server.sent[0].params?.clientUserMessageId).toBe("string");

    // The server says the queue changed; the window lists it and shows it above the composer.
    let state = reduce(initialState, { type: "queue", threadId: "t", items: await listQueue(server.client, "t") });
    expect(state.queues.t.map((item) => item.id)).toEqual(["q1"]);

    // The turn ends; the queue extension starts the next turn from the queued message and empties it.
    queue.length = 0;
    const started = { method: "turn/started", params: { threadId: "t", turn: { id: "turn-2", status: "inProgress", items: [], error: null } } } as unknown as ServerNotification;
    state = reduce(state, { type: "notification", notification: started });
    state = reduce(state, { type: "queue", threadId: "t", items: await listQueue(server.client, "t") });
    expect(state.queues.t).toEqual([]);
  });

  it("a queued message can be sent into the running turn now", async () => {
    const server = new ScriptedServer({ "turn/steer": () => ({ turnId: "turn-1" }), "thread/queue/delete": () => ({}) });
    await steerQueued(server.client, "t", "turn-1", { id: "q1", input: [text("now")], clientUserMessageId: "c" });
    expect(server.methods()).toEqual(["turn/steer", "thread/queue/delete"]);
  });

  it("a message with no turn running starts one, with the chosen effort", async () => {
    const server = new ScriptedServer({ "turn/start": () => ({ turn: { id: "turn-9" } }) });
    await sendMessage(server.client, { threadId: "t", activeTurnId: null, input: [text("go")], followUp: "queue", permissions: ":workspace", effort: "high" });
    expect(server.sent[0]).toEqual({ method: "turn/start", params: { threadId: "t", input: [text("go")], permissions: ":workspace", effort: "high" } });
  });
});

describe("editing, forking and the picker", () => {
  const turns: ThreadView["turns"] = [
    { id: "turn-1", status: "completed", error: null, diff: null, items: [{ type: "userMessage", id: "u1", content: [text("first try")] } as never] },
    { id: "turn-2", status: "completed", error: null, diff: null, items: [{ type: "userMessage", id: "u2", content: [text("fix the retry test")] } as never, { type: "agentMessage", id: "a2", text: "done" } as never] },
  ];

  it("Esc Esc takes the last message back: the thread is reverted to before it and its text returns", async () => {
    expect(lastUserTurn(threadView(turns))).toEqual({ turnId: "turn-2", text: "fix the retry test" });
    expect(userTexts(threadView(turns))).toEqual(["first try", "fix the retry test"]);
    const server = new ScriptedServer({ "thread/revert": () => ({ thread: {} }) });
    expect(await takeBack(server.client, threadView(turns))).toBe("fix the retry test");
    expect(server.sent).toEqual([{ method: "thread/revert", params: { threadId: "t", beforeTurnId: "turn-2" } }]);
    // Never while a turn runs.
    expect(await takeBack(server.client, threadView(turns, "turn-2"))).toBeNull();
  });

  it("fork copies the thread into a new one", async () => {
    const server = new ScriptedServer({ "thread/fork": (params) => ({ thread: { id: "t-copy", forkedFromId: params.threadId } }) });
    const copy = await forkThread(server.client, "t", "qwen3.8-27b");
    expect(copy.id).toBe("t-copy");
    expect(server.sent[0]).toEqual({ method: "thread/fork", params: { threadId: "t", model: "qwen3.8-27b" } });
  });

  it("the picker offers the efforts the served model's catalog entry allows, and remembers recent models", () => {
    expect(effortChoices(model)).toEqual({ efforts: ["low", "medium", "high"], preferred: "medium" });
    expect(effortFor(model, "high")).toBe("high");
    expect(effortFor(model, "xhigh")).toBeNull();
    expect(effortFor(null, "high")).toBeNull();
    expect(rememberModel(["a", "b", "c", "d", "e"], "c")).toEqual(["c", "a", "b", "d", "e"]);
    expect(rememberModel(["a", "b", "c", "d", "e"], "f")).toEqual(["f", "a", "b", "c", "d"]);
  });
});

describe("Plan mode", () => {
  it("rides on turn/start as the plan collaboration mode with the served model and effort", async () => {
    expect(collaborationMode("default", "qwen", "high")).toBeNull();
    expect(collaborationMode("plan", null, null)).toBeNull();
    const mode = collaborationMode("plan", "qwen", "high");
    expect(mode).toEqual({ mode: "plan", settings: { model: "qwen", reasoning_effort: "high", developer_instructions: null } });
    const server = new ScriptedServer({ "turn/start": () => ({ turn: { id: "x" } }) });
    await sendMessage(server.client, { threadId: "t", activeTurnId: null, input: [text("plan the migration")], followUp: "steer", collaborationMode: mode });
    expect(server.sent[0].params).toMatchObject({ collaborationMode: { mode: "plan" } });
    expect(parseSlash("/plan move the cache")).toMatchObject({ command: { name: "plan" }, args: "move the cache" });
  });
});

describe("Settings › Appearance", () => {
  it("lets only colours and plain font lists reach the stylesheet", () => {
    expect(cleanAppearance({ accent: "#0abab5", canvas: "red", text: "#123", fontUi: "Inter, sans-serif", fontCode: "x; } body { display: none" }))
      .toEqual({ accent: "#0abab5", text: "#123", fontUi: "Inter, sans-serif" });
    expect(cleanAppearance(null)).toEqual({});
  });
});

describe("the goal row", () => {
  it("follows the server's goal notifications, and reads its time", () => {
    const goal = { threadId: "t", objective: "Every test passes", status: "active", tokenBudget: null, tokensUsed: 1200, timeUsedSeconds: 75, createdAt: 1, updatedAt: 2 };
    let state = reduce(initialState, { type: "notification", notification: { method: "thread/goal/updated", params: { threadId: "t", turnId: null, goal } } as unknown as ServerNotification });
    expect(state.goals.t?.objective).toBe("Every test passes");
    state = reduce(state, { type: "notification", notification: { method: "thread/goal/cleared", params: { threadId: "t" } } as unknown as ServerNotification });
    expect(state.goals.t).toBeNull();
    expect([goalTime(42), goalTime(75), goalTime(3725)]).toEqual(["42s", "1m", "1h 2m"]);
    expect(parseSlash("/goal every test passes")).toMatchObject({ command: { name: "goal" }, args: "every test passes" });
  });
});

describe("/review", () => {
  it("names uncommitted changes, a base branch, a commit, or instructions of its own", () => {
    expect(reviewTarget("")).toEqual({ type: "uncommittedChanges" });
    expect(reviewTarget("base main")).toEqual({ type: "baseBranch", branch: "main" });
    expect(reviewTarget("commit 1a2b3c4")).toEqual({ type: "commit", sha: "1a2b3c4", title: null });
    expect(reviewTarget("check the retry loop for races")).toEqual({ type: "custom", instructions: "check the retry loop for races" });
    expect(parseSlash("/review base main")).toMatchObject({ command: { name: "review" }, args: "base main" });
  });
});
