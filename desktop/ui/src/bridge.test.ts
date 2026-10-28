// The page's two hosts (bridge.ts): in the app, an Ask attachment goes to the main process as
// base64 over the one channel, since `app://` has no `/api/upload`; and the app's menu can switch
// the view. No window: `window` is a stub for the length of each case.

import { afterEach, describe, expect, it } from "vitest";

import {
  alwaysOnTop, applyOpenTarget, base64, canRecord, consumeOpenTarget, docs, fileSize, forgeAvailable, git, host,
  incognito, knowledge, listenBridge, mcpBundles, memoryFiles, night, nodes, openDocument, openTarget, openWebLink,
  parseOpenTarget, pathForFile, popOut, preview, prs, quitApp, readBoards, recall, relaunchApp, telegram, terminals,
  toggleFullScreen, transcribe, upload, windowControl, windows, workAttach, worktrees,
} from "./bridge";
import { recordCheckout, resetChatRepos, setActiveChatRepoCwd } from "./repos";
import { RpcClient, RpcError } from "./rpc";

type Listener = (event: { data: unknown }) => void;

function stubWindow(type: string | undefined) {
  const sent: unknown[] = [];
  const listeners: Listener[] = [];
  (globalThis as { window?: unknown }).window = {
    mightlingWindowType: type,
    electronBridge: {
      sendMessageFromView: async (message: unknown) => {
        sent.push(message);
        if ((message as { type?: string }).type === "voice/transcribe") return { text: "Hello Ask" };
        return { path: "/home/u/.mightling/ask/q-0123456789abcdef/shot.png", bytes: 3 };
      },
    },
    addEventListener: (_name: string, listener: Listener) => listeners.push(listener),
    removeEventListener: () => {},
  };
  return { sent, listeners };
}

afterEach(() => {
  resetChatRepos();
  delete (globalThis as { window?: unknown }).window;
});

describe("the app's host", () => {
  it("sends an Ask attachment to the main process, base64, never to /api/upload", async () => {
    const { sent } = stubWindow("electron");
    expect(host()).toBe("electron");
    const file = new Blob([new Uint8Array([0x89, 0x50, 0x4e])], { type: "image/png" });
    const landed = await upload("thread-1", file, "image", "shot.png");
    expect(landed.path).toMatch(/shot\.png$/);
    expect(sent).toEqual([{ type: "ask/upload", thread: "thread-1", name: "shot.png", kind: "image", data: "iVBO" }]);
  });

  it("encodes large files whole", async () => {
    const bytes = new Uint8Array(100_000).map((_, index) => index % 251);
    const expected = btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""));
    expect(await base64(new Blob([bytes]))).toBe(expected);
  });

  it("switches the view when the app's menu asks", async () => {
    const { listeners } = stubWindow("electron");
    const views: string[] = [];
    await listenBridge({ message: () => {}, stderr: () => {}, protocolError: () => {}, exit: () => {}, view: (view) => views.push(view) });
    for (const listener of listeners) {
      listener({ data: { channel: "view", payload: "ask" } });
      listener({ data: { channel: "view", payload: "work" } });
    }
    expect(views).toEqual(["ask", "work"]);
  });

  it("is a browser everywhere else", () => {
    stubWindow(undefined);
    expect(host()).toBe("web");
  });

  it("keeps git.available() true in the desktop app and false in a browser outside chat-repos threads", () => {
    stubWindow("electron");
    expect(git.available()).toBe(true);
    expect(git.available("/home/u/src/shop")).toBe(true);

    stubWindow(undefined);
    expect(git.available()).toBe(false);
    expect(git.available(null)).toBe(false);
    expect(git.available("/home/u/src/shop")).toBe(false);

    const checkout = "/home/u/.mightling/chat-repos/org/shop/web-01234567";
    recordCheckout({ thread: "01234567", cwd: checkout, repo: "org/shop", branch: "phone/01234567", read_only: false });
    expect(git.available("/home/u/src/shop")).toBe(false);
    expect(git.available(checkout)).toBe(true);

    // Without a folder named it stays the desktop app's answer, whatever thread is shown.
    setActiveChatRepoCwd(checkout);
    expect(git.available()).toBe(false);
  });
});

describe("dictation", () => {
  it("goes to the app's main process as base64, and to /api/transcribe in a browser", async () => {
    const { sent } = stubWindow("electron");
    const recording = new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm;codecs=opus" });
    expect(await transcribe(recording)).toBe("Hello Ask");
    expect(sent).toEqual([{ type: "voice/transcribe", mime: "audio/webm;codecs=opus", data: "AQID" }]);

    stubWindow(undefined);
    const posted: { url: string; init: RequestInit }[] = [];
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      posted.push({ url, init });
      return new Response(JSON.stringify({ text: "From the web" }), { status: 200 });
    }) as unknown as typeof fetch;
    try {
      expect(await transcribe(recording)).toBe("From the web");
      expect(posted[0].url).toBe("/api/transcribe");
      expect((posted[0].init.headers as Record<string, string>)["Content-Type"]).toBe("audio/webm;codecs=opus");
      globalThis.fetch = (async () => new Response(JSON.stringify({ error: "Speech-to-text is not running" }), { status: 503 })) as unknown as typeof fetch;
      await expect(transcribe(recording)).rejects.toThrow("not running");
    } finally {
      globalThis.fetch = realFetch;
    }
  });

  it("is offered only in a secure context with a microphone API", () => {
    stubWindow(undefined);
    expect(canRecord()).toBe(false);
    (globalThis as { window?: { isSecureContext?: boolean } }).window!.isSecureContext = true;
    const media = Object.getOwnPropertyDescriptor(globalThis, "navigator");
    Object.defineProperty(globalThis, "navigator", { value: { mediaDevices: { getUserMedia: () => {} } }, configurable: true });
    (globalThis as { MediaRecorder?: unknown }).MediaRecorder = class {};
    try {
      expect(canRecord()).toBe(true);
    } finally {
      delete (globalThis as { MediaRecorder?: unknown }).MediaRecorder;
      if (media) Object.defineProperty(globalThis, "navigator", media);
    }
  });
});

describe("RpcClient and window/IPC bridge routes", () => {
  it("correlates requests, runs beforeRequest/afterResponse hooks, dispatches server requests and notifications, and fails pending calls", async () => {
    const outgoing: unknown[] = [];
    const seenEvents: unknown[] = [];
    const client = new RpcClient({
      send: async (msg) => {
        outgoing.push(msg);
      },
    });
    client.onNotification = (n) => seenEvents.push(n);
    client.onServerRequest = (r) => seenEvents.push(r);
    const beforeSeen: string[] = [];
    client.beforeRequest = async (method) => { beforeSeen.push(method); };
    const afterSeen: string[] = [];
    client.afterResponse = (method) => afterSeen.push(method);

    const p1 = client.request("thread/list", { limit: 5 });
    await Promise.resolve();
    expect(outgoing[0]).toEqual({ id: 1, method: "thread/list", params: { limit: 5 } });
    client.receive({ id: 1, result: { data: [] } });
    expect(await p1).toEqual({ data: [] });
    expect(beforeSeen).toEqual(["thread/list"]);
    expect(afterSeen).toEqual(["thread/list"]);

    await client.initialized();
    expect(outgoing[1]).toEqual({ method: "initialized" });
    await client.respond("srv-1", { decision: "accept" });
    expect(outgoing[2]).toEqual({ id: "srv-1", result: { decision: "accept" } });
    await client.respondError("srv-2", -32601, "unsupported");
    expect(outgoing[3]).toEqual({ id: "srv-2", error: { code: -32601, message: "unsupported" } });

    client.receive("not-an-object");
    client.receive({ id: 999, result: {} });
    client.receive({ id: "srv-3", method: "item/tool/call", params: {} });
    client.receive({ method: "turn/started", params: {} });
    expect(seenEvents).toHaveLength(2);

    const pErr = client.request("turn/start", {} as never);
    await Promise.resolve();
    client.receive({ id: 2, error: { code: 400, message: "bad turn" } });
    await expect(pErr).rejects.toBeInstanceOf(RpcError);

    const pFail = client.request("thread/read", {} as never);
    await Promise.resolve();
    client.fail("connection closed");
    await expect(pFail).rejects.toThrow("connection closed");

    const throwingClient = new RpcClient({ send: () => Promise.reject(new Error("pipe broken")) });
    await expect(throwingClient.request("thread/list", {})).rejects.toThrow("pipe broken");
  });

  it("routes multi-window, terminal, git, worktree, PR, preview, docs and bridge events in electron and web hosts", async () => {
    const { sent, listeners } = stubWindow("electron");
    expect(windows.available()).toBe(true);
    await windows.open("work");
    await windows.state();
    await windows.showing("work", "t1");
    await windows.take("t1");
    await windows.showThread("t1");
    await windows.detach("terminal", "t1");
    await windows.dock("terminal", "t1");
    await windows.relay({ kind: "newTerminal" });

    expect(terminals.available()).toBe(true);
    await terminals.list();
    await terminals.publish({ processId: "term-1", title: "shell", cwd: "/repo", threadId: "t1", exitCode: null, error: null });
    await terminals.forget("term-1");
    await terminals.backlog("term-1");

    await git.repos("/repo");
    await git.missing();
    await git.status("/repo");
    await git.diff("/repo", "unstaged", null);
    await git.log("/repo");
    await git.changes("/repo");
    await git.stage("/repo", ["a.ts"]);
    await git.unstage("/repo", ["a.ts"]);
    await git.discard("/repo", ["a.ts"]);
    await git.apply("/repo", "diff", { cached: true });
    await git.commit("/repo", "msg", true);
    await git.push("/repo", false, "t1");
    await git.pullRequest("/repo", "t1");

    await worktrees.list("/repo", "/wt");
    await worktrees.create("/repo", "main", true, "/wt");
    await worktrees.remove("/repo", "/wt/1", "/wt", null);
    await worktrees.restore("/repo", "abcdef0", "/wt");
    await worktrees.branch("/wt/1", "feat/x");
    await worktrees.handOff("toLocal", "/wt/1", "/repo", "/wt", "t1");
    await worktrees.bind("/wt/1", "t1");
    await worktrees.branches("/repo");

    expect(prs.available()).toBe(true);
    await prs.status("/repo");
    await prs.list("/repo", "review", "t1");
    await prs.view("/repo", 1, "t1");
    await prs.activity("/repo", 1, "t1");
    await prs.diff("/repo", 1, "t1");
    await prs.review("/repo", 1, "approve", "", "t1");
    await prs.create("/repo", { title: "t", body: "", base: null, draft: false }, "t1");

    expect(preview.available()).toBe(true);
    await preview.open("p1", "http://127.0.0.1:3000", "t1");
    await preview.navigate("p1", "http://127.0.0.1:3000/a", "t1");
    await preview.go("p1", "back");
    await preview.reload("p1", true);
    await preview.place("p1", { x: 0, y: 0, width: 100, height: 100 }, true);
    await preview.pick("p1");
    await preview.allowOnce("p1", "example.com");
    await preview.devTools("p1");
    await preview.close("p1");
    await preview.clear();
    await preview.console("t1");
    const previewEvents: unknown[] = [];
    const unlistenPreview = preview.listen((ev) => previewEvents.push(ev));
    for (const l of listeners) l({ data: { channel: "preview://event", payload: { id: "p1", kind: "state" } } });
    expect(previewEvents).toEqual([{ id: "p1", kind: "state" }]);
    unlistenPreview();

    await docs.status();
    await docs.add("/docs");
    await docs.remove("notes");
    await docs.reindex("notes");

    await openDocument("notes/42", 10);
    await expect(openDocument("../bad", null)).rejects.toThrow("not a document");
    await openWebLink("https://example.com", "t1");
    await fileSize("/repo/a.ts");
    expect(pathForFile({ name: "a.ts" } as File)).toBeNull();
    await windowControl("minimize");
    await toggleFullScreen();
    await quitApp();
    await relaunchApp();
    await popOut("t1");
    await alwaysOnTop(true);
    await workAttach(new Blob(["x"]), "image", "a.png");
    expect(incognito.available()).toBe(true);
    await incognito.mark("t1");
    await incognito.forget("t1");
    expect(memoryFiles.available()).toBe(true);
    await memoryFiles.list();
    await memoryFiles.save("MEMORY.md", "# test");
    expect(mcpBundles.available()).toBe(true);
    await mcpBundles.list();
    await mcpBundles.pick();
    await mcpBundles.install("tok", {}, false);
    await mcpBundles.remove("b1");
    expect(recall.available()).toBe(true);
    await recall.index();
    await recall.write("t1", 1, "# md");
    await recall.prune(["t1"]);
    await nodes.list();
    await nodes.use("local");
    await night.list();
    await night.report("2026-10-20.md", true);
    await night.add("/repo", "task", "pytest", { bestOf: 3 });
    await night.status();
    await night.drop("/repo", "n1");
    await night.runNow();
    await night.every("/repo", "task", null, "daily", {});
    await night.repeating();
    await night.dropRepeating("r1");
    await night.details("n1", false);
    await night.apply("/repo", "n1");
    await night.note("n1");
    await night.archive("query");
    expect(knowledge.available()).toBe(true);
    await knowledge.list("p1");
    await knowledge.add("p1");
    await knowledge.remove("p1", "doc.md");
    await readBoards(null);
    expect(telegram.available()).toBe(false);
    expect(sent.length).toBeGreaterThan(50);

    const events: string[] = [];
    const unlisten = await listenBridge({
      message: () => events.push("msg"),
      stderr: () => events.push("stderr"),
      protocolError: () => events.push("proto"),
      exit: () => events.push("exit"),
      link: () => events.push("link"),
      task: () => events.push("task"),
      windows: () => events.push("windows"),
      terminals: () => events.push("terminals"),
      docked: () => events.push("docked"),
      paneAction: () => events.push("action"),
      syncNotice: () => events.push("sync"),
      openTarget: () => events.push("target"),
      command: () => events.push("cmd"),
    });
    for (const [channel, payload] of [
      ["work://message", {}],
      ["work://stderr", "err"],
      ["work://protocol-error", "bad"],
      ["work://exit", 0],
      ["link", {}],
      ["task://event", {}],
      ["windows", {}],
      ["terminals", []],
      ["pane://docked", { pane: "terminal", thread: "t1" }],
      ["pane://action", {}],
      ["sync://notice", {}],
      ["open-target", { kind: "repos", approval: false }],
      ["command", "newChat"],
    ] as const) {
      for (const l of listeners) l({ data: { channel, payload } });
    }
    expect(events).toEqual([
      "msg", "stderr", "proto", "exit", "link", "task", "windows",
      "terminals", "docked", "action", "sync", "target", "cmd",
    ]);
    unlisten();

    expect(parseOpenTarget("?open=repos&approval=1")).toEqual({ kind: "repos", approval: true });
    expect(parseOpenTarget("?open=repo:acme/shop")).toEqual({ kind: "repo", repo: "acme/shop", approval: false });
    expect(parseOpenTarget("?open=thread:01234567&approval=1")).toEqual({ kind: "thread", threadId: "01234567", approval: true });
    expect(parseOpenTarget("?open=repo:../bad")).toBeNull();
    expect(parseOpenTarget("?other=1")).toBeNull();
    const seenTargets: string[] = [];
    applyOpenTarget(
      { kind: "repo", repo: "acme/shop", approval: true },
      { openRepos: (r) => seenTargets.push(`repo:${r}`), openWorkThread: (t) => seenTargets.push(`thread:${t}`), setApprovalRequested: (a) => seenTargets.push(`approval:${a}`) },
    );
    expect(seenTargets).toEqual(["approval:true", "repo:acme/shop"]);
    expect(consumeOpenTarget({ pathname: "/", search: "?open=repos&approval=1&keep=1", hash: "#work" }, { replaceState: () => {} })).toEqual({ kind: "repos", approval: true });
    expect(openTarget()).toBeNull();

    // Web host fallbacks:
    stubWindow(undefined);
    expect(windows.available()).toBe(false);
    expect(terminals.available()).toBe(false);
    expect(prs.available()).toBe(false);
    expect(preview.available()).toBe(false);
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string) => {
      if (url === "/api/forge") return new Response(JSON.stringify({ available: true, control: true }), { status: 200 });
      return new Response(JSON.stringify({ collections: [], runtime_missing: null }), { status: 200 });
    }) as unknown as typeof fetch;
    try {
      expect(await forgeAvailable()).toBe(true);
      expect((await docs.status()).collections).toEqual([]);
      expect(await readBoards(null)).toEqual([]);
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});
