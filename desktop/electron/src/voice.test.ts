import { describe, expect, it } from "vitest";

import { SPEECH_MODEL, SPEECH_URL, transcribe } from "./voice";

const base64 = (text: string) => Buffer.from(text).toString("base64");

describe("dictation", () => {
  it("posts the recording to the speech-to-text service on loopback and answers its text", async () => {
    const calls: { url: string; form: FormData }[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      calls.push({ url, form: init.body as FormData });
      return new Response(JSON.stringify({ text: "  Hello Ask " }), { status: 200 });
    }) as unknown as typeof fetch;
    expect(await transcribe({ mime: "audio/webm;codecs=opus", data: base64("OPUS") }, fetcher)).toEqual({ text: "Hello Ask" });
    expect(calls[0].url).toBe(SPEECH_URL);
    expect(SPEECH_URL.startsWith("http://127.0.0.1:")).toBe(true);
    expect(calls[0].form.get("model")).toBe(SPEECH_MODEL);
    const file = calls[0].form.get("file") as File;
    expect(file.name).toBe("speech.webm");
    expect(file.type).toBe("audio/webm");
    expect(Buffer.from(await file.arrayBuffer()).toString()).toBe("OPUS");
  });

  it("refuses what is not audio, an empty recording, and says what to start when nothing answers", async () => {
    const never = (async () => {
      throw new Error("not reached");
    }) as unknown as typeof fetch;
    await expect(transcribe({ mime: "text/plain", data: base64("x") }, never)).rejects.toThrow("not audio");
    await expect(transcribe({ mime: "audio/webm", data: "" }, never)).rejects.toThrow("empty");
    const refused = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    await expect(transcribe({ mime: "audio/webm", data: base64("x") }, refused)).rejects.toThrow("ling-admin voice start");
    const failing = (async () => new Response("no", { status: 500 })) as unknown as typeof fetch;
    await expect(transcribe({ mime: "audio/webm", data: base64("x") }, failing)).rejects.toThrow("HTTP 500");
  });
});
