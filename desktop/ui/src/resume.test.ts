// Tests for resuming the selected thread when the webview returns from the background (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §10 G10).
import { describe, expect, it, vi } from "vitest";

import type { Thread } from "./protocol/v2/Thread";
import type { Turn } from "./protocol/v2/Turn";
import { attachVisibilityResume, type ResumeDocumentLike, type ResumeEvent } from "./resume";
import { initialState, reduce } from "./store";

function createFakeDocument(initialVisibility: "visible" | "hidden" = "visible"): ResumeDocumentLike & {
  visibilityState: "visible" | "hidden";
  emit: (type: string, event?: ResumeEvent) => void;
} {
  const listeners = new Map<string, Set<(event?: ResumeEvent) => void>>();
  return {
    visibilityState: initialVisibility,
    addEventListener(type, listener) {
      let set = listeners.get(type);
      if (!set) {
        set = new Set();
        listeners.set(type, set);
      }
      set.add(listener);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    emit(type, event) {
      for (const listener of listeners.get(type) ?? []) {
        listener(event);
      }
    },
  };
}

function makeTurn(id: string, text: string): Turn {
  return {
    id,
    status: "completed",
    error: null,
    items: [{ type: "agentMessage", id: `msg-${id}`, text, phase: null }],
  } as unknown as Turn;
}

function makeThread(id: string, turns: Turn[]): Thread {
  return {
    id,
    preview: "Check status",
    ephemeral: false,
    modelProvider: "openai-custom",
    createdAt: 1,
    updatedAt: 2,
    status: { type: "idle" },
    path: null,
    cwd: "/home/mightling/chat-repos/app",
    cliVersion: "0.158.0",
    source: "appServer",
    agentNickname: null,
    agentRole: null,
    gitInfo: null,
    name: "Repo session",
    turns,
  } as unknown as Thread;
}

describe("attachVisibilityResume (G10)", () => {
  it("resumes once on a visible event and merges missed turns from thread/turns/list into the store", async () => {
    const doc = createFakeDocument("visible");
    const turn1 = makeTurn("turn-1", "Before background");
    const turn2 = makeTurn("turn-2", "Finished while hidden");
    const turn3 = makeTurn("turn-3", "Running when the page came back");
    const baseThread = makeThread("thread-1", [turn1]);

    let state = reduce(initialState, { type: "opened", thread: baseThread });
    const markLoaded = vi.fn();

    const request = vi.fn(async (method: string, _params: unknown) => {
      if (method === "thread/resume") {
        return { thread: baseThread };
      }
      if (method === "thread/turns/list") {
        // Newest first, and only the newest page is read.
        return { data: [turn3, turn2], nextCursor: "page-2" };
      }
      return {};
    });

    const detach = attachVisibilityResume({
      host: "web",
      request,
      document: doc,
      getState: () => ({
        server: "ready",
        selectedThreadId: state.selected,
        servedModel: "qwen3.8-27b-nvfp4-dflash2",
        thread: state.threads["thread-1"]?.thread ?? null,
      }),
      onThreadOpened: (thread) => {
        state = reduce(state, { type: "opened", thread });
      },
      markLoaded,
    });

    doc.emit("visibilitychange");
    await vi.waitFor(() => {
      expect(state.threads["thread-1"].turns.map((t) => t.id)).toEqual(["turn-1", "turn-2", "turn-3"]);
    });

    expect(request).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenNthCalledWith(1, "thread/resume", {
      threadId: "thread-1",
      model: "qwen3.8-27b-nvfp4-dflash2",
    });
    expect(request).toHaveBeenNthCalledWith(2, "thread/turns/list", {
      threadId: "thread-1",
      limit: 100,
      itemsView: "full",
    });
    expect(markLoaded).toHaveBeenCalledWith("thread-1");
    detach();
  });

  it("does nothing on a hidden visibilitychange event or a non-persisted pageshow event", async () => {
    const doc = createFakeDocument("hidden");
    const request = vi.fn(async () => ({}));
    const onThreadOpened = vi.fn();

    const detach = attachVisibilityResume({
      host: "web",
      request,
      document: doc,
      getState: () => ({
        server: "ready",
        selectedThreadId: "thread-1",
        servedModel: "qwen3.8-27b-nvfp4-dflash2",
      }),
      onThreadOpened,
    });

    doc.emit("visibilitychange");
    doc.emit("pageshow", { persisted: false });
    await Promise.resolve();

    expect(request).not.toHaveBeenCalled();
    expect(onThreadOpened).not.toHaveBeenCalled();
    detach();
  });

  it("debounces so two quick events while a resume is in flight resume only once", async () => {
    const doc = createFakeDocument("visible");
    const baseThread = makeThread("thread-1", [makeTurn("turn-1", "Hello")]);

    let resolveResume: ((value: unknown) => void) | null = null;
    const request = vi.fn((method: string) => {
      if (method === "thread/resume") {
        return new Promise((resolve) => {
          resolveResume = resolve;
        });
      }
      return Promise.resolve({ data: [makeTurn("turn-1", "Hello")], nextCursor: null });
    });
    const onThreadOpened = vi.fn();

    const detach = attachVisibilityResume({
      host: "web",
      request,
      document: doc,
      getState: () => ({
        server: "ready",
        selectedThreadId: "thread-1",
        servedModel: "qwen3.8-27b-nvfp4-dflash2",
      }),
      onThreadOpened,
    });

    // Two quick events before the first resume finishes.
    doc.emit("visibilitychange");
    doc.emit("visibilitychange");
    doc.emit("pageshow", { persisted: true });

    expect(request).toHaveBeenCalledTimes(1);

    resolveResume!({ thread: baseThread });
    await vi.waitFor(() => {
      expect(onThreadOpened).toHaveBeenCalledTimes(1);
    });
    expect(request).toHaveBeenCalledTimes(2); // 1 thread/resume + 1 thread/turns/list
    detach();
  });

  it("does nothing in the Electron host", async () => {
    const doc = createFakeDocument("visible");
    const request = vi.fn(async () => ({}));
    const onThreadOpened = vi.fn();

    const detach = attachVisibilityResume({
      host: "electron",
      request,
      document: doc,
      getState: () => ({
        server: "ready",
        selectedThreadId: "thread-1",
        servedModel: "qwen3.8-27b-nvfp4-dflash2",
      }),
      onThreadOpened,
    });

    doc.emit("visibilitychange");
    doc.emit("pageshow", { persisted: true });
    await Promise.resolve();

    expect(request).not.toHaveBeenCalled();
    expect(onThreadOpened).not.toHaveBeenCalled();
    detach();
  });
});
