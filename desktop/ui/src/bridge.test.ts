// The page's two hosts (bridge.ts): in the app, an Ask attachment goes to the main process as
// base64 over the one channel, since `app://` has no `/api/upload`; and the app's menu can switch
// the view. No window: `window` is a stub for the length of each case.

import { afterEach, describe, expect, it } from "vitest";

import { base64, canRecord, host, listenBridge, transcribe, upload } from "./bridge";

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
