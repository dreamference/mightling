import { describe, expect, it } from "vitest";

import { dictionaryPrompt, PROMPT_CAP, SPEECH_MODEL, SPEECH_URL, transcribe } from "./voice";

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
    // A service that is running but slow is not reported as stopped.
    const slow = (async () => {
      throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
    }) as unknown as typeof fetch;
    await expect(transcribe({ mime: "audio/webm", data: base64("x") }, slow)).rejects.toThrow("did not answer within 5 minutes");
    const failing = (async () => new Response("no", { status: 500 })) as unknown as typeof fetch;
    await expect(transcribe({ mime: "audio/webm", data: base64("x") }, failing)).rejects.toThrow("HTTP 500");
  });

  it("gives the service the user's word list as its prompt, tidied and capped (DESKTOP §16 B7)", async () => {
    expect(dictionaryPrompt(["Mightling", "  fetcher/client.py ", "mightling", "", 3, "ConnectionError"])).toBe("Mightling, fetcher/client.py, ConnectionError");
    expect(dictionaryPrompt([])).toBeNull();
    expect(dictionaryPrompt("Mightling")).toBeNull();
    const many = Array.from({ length: 200 }, (_, index) => `symbol_number_${index}`);
    expect(dictionaryPrompt(many)!.length).toBeLessThanOrEqual(PROMPT_CAP);
    const forms: FormData[] = [];
    const fetcher = (async (_url: string, init: RequestInit) => {
      forms.push(init.body as FormData);
      return new Response(JSON.stringify({ text: "ok" }), { status: 200 });
    }) as unknown as typeof fetch;
    await transcribe({ mime: "audio/webm", data: base64("OPUS") }, fetcher, ["GB10", "NVFP4"]);
    await transcribe({ mime: "audio/webm", data: base64("OPUS") }, fetcher);
    expect(forms[0].get("prompt")).toBe("GB10, NVFP4");
    expect(forms[1].get("prompt")).toBeNull();
  });
});
