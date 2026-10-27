// Threads from other devices (specs/DREAMFERENCE_MIGHTLING_SYNC.md, Phases 1 and 2): the page's side
// of the node's sync hub, over a stand-in `fetch`, and the live thread's pure logic (§18.4).
import { describe, expect, it } from "vitest";

import {
  HubRefusal, SETTLE_WITHIN, ago, approvalOf, approveAsk, awaitCommand, banner, byDevice, canControl, elsewhere, emptyLive, folderName, foldLive, hub, liveShown,
  queuedLabel, sendAsk, settleLive, startAsk, startDevices, stopAsk,
  type LiveMessage, type SyncCommand, type SyncDevice, type SyncStatus, type SyncedItem, type SyncedThread,
} from "./sync";

const thread = (id: string, owner: string, updated: number, archived = false): SyncedThread => ({
  thread: id, owner, owner_name: `${owner}'s`, title: `t ${id}`, archived, cwd: "/home/u/app", source: "vscode", created: 1, updated, fork_of: null,
});

function stand_in(answers: Record<string, [number, unknown]>) {
  const sent: { url: string; init?: RequestInit }[] = [];
  const fetcher = async (url: string, init?: RequestInit) => {
    sent.push({ url, init });
    const [status, body] = answers[url.split("?")[0]] ?? [404, { error: "Not found." }];
    return new Response(JSON.stringify(body), { status });
  };
  return { sent, api: hub(fetcher) };
}

describe("threads from other devices", () => {
  it("leaves this machine's own threads out, groups the rest by device and puts the newest first", () => {
    const threads = [thread("a", "lap", 10), thread("b", "node", 30), thread("c", "desk", 20), thread("d", "lap", 40), thread("e", "lap", 50, true)];
    const groups = byDevice(threads, "node", false);
    expect(groups.map((group) => [group.name, group.threads.map((t) => t.thread)])).toEqual([["lap's", ["d", "a"]], ["desk's", ["c"]]]);
    expect(byDevice(threads, "node", true).map((group) => group.threads.map((t) => t.thread))).toEqual([["e"]]);
    expect(elsewhere(threads, "node")).toBe(3);
    expect(elsewhere(threads, null)).toBe(4);
  });

  it("is nothing while the hub is off: its routes answer 404", async () => {
    const { api } = stand_in({});
    expect(await api.status()).toBeNull();
    const { api: on } = stand_in({ "/api/sync/status": [200, { enabled: true, cursor: 3, this_device: null, caller: { device: "ph", agent: false, scope: "watch" } }] });
    expect((await on.status())?.caller.scope).toBe("watch");
  });

  it("archives and deletes through the hub, and says why the hub refused", async () => {
    const { api, sent } = stand_in({
      "/api/sync/threads/x/meta": [200, {}],
      "/api/sync/threads/x/delete": [403, { error: "this device may watch threads, not delete them" }],
      "/api/sync/manifest": [200, { cursor: 9 }],
    });
    await api.archive("x", true);
    expect(sent[0].init?.method).toBe("POST");
    expect(JSON.parse(String(sent[0].init?.body))).toEqual({ archived: true });
    await expect(api.remove("x")).rejects.toThrow("may watch threads, not delete them");
    expect(await api.changed(4)).toBe(9);
    expect(sent[2].url).toBe("/api/sync/manifest?since=4&wait=25");
  });

  it("names times and folders briefly", () => {
    expect(ago(1000, 1030)).toBe("just now");
    expect(ago(1000, 1000 + 180)).toBe("3 min ago");
    expect(ago(0, 7200)).toBe("2 h ago");
    expect(ago(0, 3 * 86400)).toBe("3 days ago");
    expect(folderName("/home/u/app/")).toBe("app");
    expect(folderName(null)).toBe("");
  });
});

// Phase 2 (§18.3, §18.4): a thread from another device, live.

const T = "thread-1";
const ev = (message: LiveMessage) => ({ message });
const started = (turn: string) => ev({ method: "turn/started", params: { threadId: T, turn: { id: turn } } });
const completed = (turn: string) => ev({ method: "turn/completed", params: { threadId: T, turn: { id: turn } } });
const delta = (itemId: string, words: string) => ev({ method: "item/agentMessage/delta", params: { threadId: T, turnId: "u", itemId, delta: words } });
const item = (method: "item/started" | "item/completed", body: Record<string, unknown>) => ev({ method, params: { threadId: T, turnId: "u", item: body } });
const user = (id: string, text: string) => ({ type: "userMessage", id, clientId: null, content: [{ type: "text", text, text_elements: [] }] });
const turnEnd: SyncedItem = { kind: "turn", text: "Turn finished" };
const said = (text: string): SyncedItem => ({ kind: "user", text });

const status = (scope: "control" | "watch", devices: SyncDevice[] = []): SyncStatus => ({
  enabled: true, cursor: 1, this_device: "node", caller: { device: "ph", agent: false, scope }, devices,
});
const device = (id: string, online: boolean, projects: string[], kind = "sync"): SyncDevice => ({
  id, name: `${id}'s`, kind, scope: "control", online, last_seen: 100, projects, models: [],
});
const command = (state: SyncCommand["state"], error: string | null = null): SyncCommand => ({
  id: 5, kind: "send", device: "lap", thread: T, text: "go on", by: "phone", at: 1, state, error,
});

describe("a thread from another device, live", () => {
  it("streams an agent message by its item id and lets the completed item replace it", () => {
    let view = foldLive(emptyLive([]), [started("u"), item("item/completed", user("i1", "fix the login")), item("item/started", { type: "agentMessage", id: "a1", text: "" }), delta("a1", "Look"), delta("a1", "ing")]);
    expect(view.turns).toHaveLength(1);
    expect(view.turns[0].items.map((entry) => [entry.kind, entry.text, Boolean(entry.streaming)])).toEqual([["user", "fix the login", false], ["agent", "Looking", true]]);
    view = foldLive(view, [delta("a2", "Other"), item("item/completed", { type: "agentMessage", id: "a1", text: "Looking at it." })]);
    expect(view.turns[0].items.map((entry) => [entry.id, entry.text, Boolean(entry.streaming)])).toEqual([["i1", "fix the login", false], ["a1", "Looking at it.", false], ["a2", "Other", true]]);
  });

  it("shows commands and file changes, and keeps what the approvals need by item id", () => {
    const view = foldLive(emptyLive([]), [
      started("u"),
      item("item/started", { type: "commandExecution", id: "c1", command: "cargo test", status: "inProgress", aggregatedOutput: null, exitCode: null }),
      item("item/completed", { type: "commandExecution", id: "c1", command: "cargo test", status: "completed", aggregatedOutput: "a\nb\nok\n", exitCode: 0 }),
      item("item/started", { type: "fileChange", id: "f1", changes: [{ path: "src/a.rs", kind: { type: "update" }, diff: "" }, { path: "src/b.rs", kind: { type: "add" }, diff: "" }], status: "inProgress" }),
      item("item/completed", { type: "reasoning", id: "r1" }),
    ]);
    expect(view.turns[0].items.map((entry) => [entry.kind, entry.text, entry.exit_code ?? null, entry.output ?? ""])).toEqual([["command", "cargo test", 0, "a\nb\nok"], ["files", "src/a.rs\nsrc/b.rs", null, ""]]);
    const files = { id: 12, method: "item/fileChange/requestApproval", params: { threadId: T, turnId: "u", itemId: "f1", startedAtMs: 1, reason: "outside the folder" } };
    expect(approvalOf(files, view)).toEqual({ kind: "files", files: ["src/a.rs", "src/b.rs"], reason: "outside the folder" });
    const run = { id: "req-7", method: "item/commandExecution/requestApproval", params: { threadId: T, itemId: "c1", cwd: "/home/u/app" } };
    expect(approvalOf(run, view)).toEqual({ kind: "command", command: "cargo test", cwd: "/home/u/app", reason: "" });
    // A tool asking for input or an elicitation is answered on the device itself.
    expect(approvalOf({ id: 9, method: "item/tool/requestUserInput", params: { threadId: T } }, view)).toBeNull();
    expect(approvalOf({ id: 10, method: "mcpServer/elicitation/request", params: {} }, view)).toBeNull();
  });

  it("replays the hub's buffer across turns and keeps only the turn still running", () => {
    const buffer = [started("u1"), item("item/completed", user("i1", "first")), completed("u1"), started("u2"), item("item/completed", user("i2", "second")), delta("a2", "Work")];
    const view = foldLive(emptyLive([said("first"), turnEnd, said("second")]), buffer, 1000, true);
    expect(view.turns.map((turn) => turn.items.map((entry) => entry.text))).toEqual([["second", "Work"]]);
    // The transcript shows up to the turn ends it had when the live turn began; the live turn owns the rest.
    expect(liveShown(view, [said("first"), turnEnd, said("second")])).toEqual({ transcript: [said("first"), turnEnd], live: view.turns[0].items });
    // Nothing running: the buffer's turns are all in the transcript already.
    expect(foldLive(emptyLive([]), buffer.slice(0, 3), 1000, true).turns).toEqual([]);
  });

  it("hands a finished turn to the transcript once the rollout has its end, or after a few seconds", () => {
    const before = [said("first"), turnEnd];
    let view = foldLive(emptyLive(before), [started("u"), item("item/completed", user("i2", "second")), completed("u")], 1000);
    expect(view.turns[0].finishedAt).toBe(1000);
    expect(view.turns[0].items.every((entry) => !entry.streaming)).toBe(true);
    // The upload has not come yet: the live turn still shows, without the transcript's half of it.
    view = settleLive(view, [...before, said("second")], 1500);
    expect(liveShown(view, [...before, said("second")]).live.map((entry) => entry.text)).toEqual(["second"]);
    expect(liveShown(view, [...before, said("second")]).transcript).toEqual(before);
    // The rollout has the turn's end: the transcript takes over.
    const after = [...before, said("second"), turnEnd];
    view = settleLive(view, after, 1600);
    expect(view.turns).toEqual([]);
    expect(view.base).toBe(2);
    expect(liveShown(view, after)).toEqual({ transcript: after, live: [] });
    // A turn whose end never shows is let go.
    const stuck = foldLive(emptyLive(before), [started("u"), completed("u")], 1000);
    expect(settleLive(stuck, before, 1000 + SETTLE_WITHIN - 1).turns).toHaveLength(1);
    expect(settleLive(stuck, before, 1000 + SETTLE_WITHIN).turns).toEqual([]);
  });

  it("keeps a finished turn until its end arrives when the next turn starts at once", () => {
    const before = [said("first"), turnEnd];
    let view = foldLive(emptyLive(before), [started("u1"), item("item/completed", user("i1", "a")), completed("u1"), started("u2"), item("item/completed", user("i2", "b"))], 1000);
    expect(view.turns.map((turn) => turn.items.map((entry) => entry.text))).toEqual([["a"], ["b"]]);
    view = settleLive(view, [...before, said("a"), turnEnd, said("b")], 1200);
    expect(view.turns.map((turn) => turn.items.map((entry) => entry.text))).toEqual([["b"]]);
    expect(liveShown(view, [...before, said("a"), turnEnd, said("b")]).transcript).toEqual([...before, said("a"), turnEnd]);
  });

  it("notes a failed turn and skips an event kept only as a stub", () => {
    const view = foldLive(emptyLive([]), [started("u"), ev({ method: "item/completed", params: { threadId: T, truncated: 300000 } }), ev({ method: "error", params: { error: { message: "the model server went away" } } })]);
    expect(view.error).toBe("the model server went away");
    expect(view.turns[0].items).toEqual([]);
  });

  it("says where the thread runs, or since when its device has been away", () => {
    const live = { owner: "lap", owner_name: "the laptop", online: true, running: false, last_seen: 1000 };
    expect(banner(live, 1010)).toBe("Running on the laptop");
    expect(banner({ ...live, running: true }, 1010)).toBe("Running on the laptop · working…");
    expect(banner({ ...live, online: false }, 1000 + 600)).toBe("the laptop is offline, last seen 10 min ago; messages wait up to 24 hours");
    expect(banner({ ...live, online: false, last_seen: 0 }, 5000)).toBe("the laptop is offline, not seen since the hub started; messages wait up to 24 hours");
  });

  it("offers a new thread only on online sync devices with folders, this one aside, and only with control", () => {
    const devices = [device("lap", true, ["/home/u/app"]), device("desk", false, ["/home/u/app"]), device("node", true, ["/home/u/x"]), device("ph", true, [], "browser"), device("mini", true, [])];
    expect(startDevices(status("control", devices)).map((known) => known.id)).toEqual(["lap"]);
    expect(startDevices(status("watch", devices))).toEqual([]);
    expect(startDevices({ ...status("control"), devices: undefined })).toEqual([]);
    expect(canControl(status("control"))).toBe(true);
    expect(canControl(status("watch"))).toBe(false);
  });

  it("follows a thread from a cursor and sends each command as the hub expects it", async () => {
    const answer = { events: [], next: 7, reset: false, running: true, turn: "u", pending: [], queued: [], owner: "lap", owner_name: "the laptop", online: true, last_seen: 1, models: ["m1"] };
    const { api, sent } = stand_in({ "/api/sync/live/thread-1": [200, answer], "/api/sync/commands": [200, { command: command("queued") }] });
    expect((await api.live(T, 3)).next).toBe(7);
    expect(sent[0].url).toBe("/api/sync/live/thread-1?after=3&wait=25");
    await api.command(sendAsk(T, "go on"));
    await api.command(sendAsk(T, "go on", "m1"));
    await api.command(approveAsk(T, { id: 7, method: "item/commandExecution/requestApproval" }, "accept"));
    await api.command(approveAsk(T, { id: "req-7", method: "item/fileChange/requestApproval" }, "decline"));
    await api.command(stopAsk(T));
    await api.command(startAsk("lap", "/home/u/app", "write the tests"));
    const bodies = sent.slice(1).map((request) => {
      expect([request.url, request.init?.method]).toEqual(["/api/sync/commands", "POST"]);
      return JSON.parse(String(request.init?.body));
    });
    expect(bodies).toEqual([
      { kind: "send", thread: T, text: "go on" },
      { kind: "send", thread: T, text: "go on", model: "m1" },
      // The request's id goes back exactly as the device sent it: a number stays a number.
      { kind: "approve", thread: T, request: 7, decision: "accept" },
      { kind: "approve", thread: T, request: "req-7", decision: "decline" },
      { kind: "stop", thread: T },
      { kind: "start", device: "lap", cwd: "/home/u/app", text: "write the tests" },
    ]);
  });

  it("shows the hub's refusals in its own words", async () => {
    const refusals: [number, string][] = [
      [403, "this device may watch threads, not answer them"],
      [409, "the device is offline: only a message waits for it"],
      [429, "10 messages already wait for this thread"],
      [400, "that approval is not waiting any more"],
    ];
    for (const [code, words] of refusals) {
      const { api } = stand_in({ "/api/sync/commands": [code, { error: words }] });
      const refused = await api.command(stopAsk(T)).catch((error: unknown) => error);
      expect(refused).toBeInstanceOf(HubRefusal);
      expect([(refused as HubRefusal).status, (refused as HubRefusal).message]).toEqual([code, words]);
    }
  });

  it("reads a command until it is done, failed or expired, and names how it went", async () => {
    const states = [command("delivered"), command("delivered"), command("failed", "the policy refused it")];
    const sent: string[] = [];
    const api = hub(async (url: string) => {
      sent.push(url);
      return new Response(JSON.stringify({ command: states.shift() }), { status: 200 });
    });
    const seen: string[] = [];
    const final = await awaitCommand(api, command("queued"), (now) => seen.push(now.state), { sleep: async () => {} });
    expect(final.state).toBe("failed");
    expect(seen).toEqual(["delivered", "failed"]);
    expect(sent).toEqual(["/api/sync/commands/5", "/api/sync/commands/5", "/api/sync/commands/5"]);
    expect(queuedLabel(final)).toBe("the policy refused it");
    expect(queuedLabel(command("waiting"))).toBe("waiting for the device");
    expect(queuedLabel(command("delivered"))).toBe("delivered");
    expect(queuedLabel(command("expired"))).toBe("the device did not come back within 24 hours");
    // A message waiting for an offline device is read less often; closing the thread stops the reading.
    const waits: number[] = [];
    const closed = new AbortController();
    const stopped = await awaitCommand(api, command("waiting"), () => {}, { signal: closed.signal, sleep: async (ms) => { waits.push(ms); closed.abort(); } });
    expect([stopped.state, waits]).toEqual(["waiting", [10000]]);
  });
});
