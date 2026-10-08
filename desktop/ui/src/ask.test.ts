// Ask threads, attachments and the copy button (specs/DREAMFERENCE_MIGHTLING_ASK.md §3, §4.4),
// without a window: the list, what a turn sends, and copying with `navigator.clipboard` removed,
// as it is on a plain-HTTP page from another device.

import { describe, expect, it } from "vitest";

import { ASK_PROMPT, askThreads, askTitle, attachmentKind, attachmentName, isAskThread, turnInput } from "./ask";
import { copyText, type ClipboardEnv } from "./clipboard";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { Thread } from "./protocol/v2/Thread";
import { initialState, projects, reduce, type Action } from "./store";

const ROOT = "/home/u/.mightling/ask";

function thread(id: string, cwd: string, updatedAt: number, extra: Partial<Thread> = {}): Thread {
  return {
    id, cwd, updatedAt, createdAt: updatedAt, recencyAt: null, ephemeral: false, parentThreadId: null,
    preview: "", name: null, turns: [], status: { type: "idle" }, model: "m", modelProvider: "p",
    ...extra,
  } as unknown as Thread;
}

describe("Ask threads", () => {
  it("are the threads in a scratch folder under the Ask root", () => {
    expect(isAskThread(thread("a", `${ROOT}/q-0123abcd0123abcd`, 1), ROOT)).toBe(true);
    expect(isAskThread(thread("a", `${ROOT}/q-0123abcd0123abcd`, 1), `${ROOT}/`)).toBe(true);
    // A project that happens to be called `ask`, or a folder elsewhere with the same shape.
    expect(isAskThread(thread("b", "/home/u/src/ask", 1), ROOT)).toBe(false);
    expect(isAskThread(thread("c", "/tmp/ask/q-0123abcd", 1), ROOT)).toBe(false);
    expect(isAskThread(thread("d", `${ROOT}/q-0123abcd/sub`, 1), ROOT)).toBe(false);
    // A host that did not name its root: the folder's shape alone.
    expect(isAskThread(thread("e", "/x/.mightling/ask/q-00ff", 1), null)).toBe(true);
    expect(isAskThread(thread("f", "/x/project", 1), null)).toBe(false);
  });

  it("are listed newest first, without subagents, and kept out of Work's projects", () => {
    const threads = [
      thread("old", `${ROOT}/q-01`, 10, { name: "Tides" }),
      thread("new", `${ROOT}/q-02`, 30, { preview: "What is a puffin?" }),
      thread("sub", `${ROOT}/q-02`, 40, { parentThreadId: "new" }),
      thread("work", "/home/u/src/app", 20),
    ];
    expect(askThreads(threads, ROOT).map((t) => t.id)).toEqual(["new", "old"]);
    expect(askThreads(threads, ROOT).map(askTitle)).toEqual(["What is a puffin?", "Tides"]);
    const state = reduce(initialState, { type: "threads", list: threads });
    expect(projects(state, ROOT).map((p) => p.cwd)).toEqual(["/home/u/src/app"]);
  });

  it("leave the lists when archived, by the answer or by the server's notification", () => {
    const listed = reduce(initialState, { type: "threads", list: [thread("a", `${ROOT}/q-01`, 1), thread("b", `${ROOT}/q-02`, 2)] });
    const opened = reduce(listed, { type: "select", threadId: "a" });
    const archived = reduce(opened, { type: "archived", threadId: "a" });
    expect(Object.keys(archived.threads)).toEqual(["b"]);
    expect(archived.selected).toBeNull();
    const notified: Action = { type: "notification", notification: { method: "thread/archived", params: { threadId: "b" } } as ServerNotification };
    expect(Object.keys(reduce(archived, notified).threads)).toEqual([]);
  });

  it("are renamed through the server's notification", () => {
    const listed = reduce(initialState, { type: "threads", list: [thread("a", `${ROOT}/q-01`, 1)] });
    const renamed = reduce(listed, { type: "notification", notification: { method: "thread/name/updated", params: { threadId: "a", threadName: "Tides" } } as ServerNotification });
    expect(renamed.threads.a.thread.name).toBe("Tides");
  });

  it("name the prompt, never send its text", () => {
    expect(ASK_PROMPT).toBe("ask");
  });
});

describe("attachments", () => {
  it("send images as localImage and name other files in the text", () => {
    const input = turnInput("What is in these?", [
      { kind: "image", path: `${ROOT}/q-01/photo.png`, name: "photo.png" },
      { kind: "file", path: `${ROOT}/q-01/report.pdf`, name: "report.pdf" },
    ]);
    expect(input).toEqual([
      { type: "text", text: `What is in these?\n\nAttached file (in this thread's folder):\n- ${ROOT}/q-01/report.pdf`, text_elements: [] },
      { type: "localImage", path: `${ROOT}/q-01/photo.png` },
    ]);
    // An image alone is a question too.
    expect(turnInput("", [{ kind: "image", path: "/p.png", name: "p.png" }])).toEqual([{ type: "localImage", path: "/p.png" }]);
  });

  it("tell images from files by type, then by name", () => {
    expect(attachmentKind("image/png", "a")).toBe("image");
    expect(attachmentKind("image/jpeg", "a")).toBe("image");
    expect(attachmentKind("", "Photo.JPG")).toBe("image");
    expect(attachmentKind("image/svg+xml", "a.svg")).toBe("file");
    expect(attachmentKind("application/pdf", "a.pdf")).toBe("file");
  });

  it("give a pasted screenshot a name", () => {
    expect(attachmentName("shot.png", "image/png", 0)).toBe("shot.png");
    expect(attachmentName("", "image/jpeg", 2)).toMatch(/^pasted-\d+-2\.jpg$/);
  });
});

describe("the copy button", () => {
  function stubDocument(result: boolean) {
    const log: string[] = [];
    const area = { value: "", style: {} as Record<string, string>, setAttribute: () => {}, select: () => log.push(`select:${area.value}`) };
    const doc = {
      createElement: () => area,
      execCommand: (command: string) => {
        log.push(`exec:${command}`);
        return result;
      },
      body: { appendChild: () => log.push("append"), removeChild: () => log.push("remove") },
    };
    return { log, env: { document: doc } as unknown as ClipboardEnv };
  }

  it("falls back to execCommand when navigator.clipboard is undefined (plain HTTP)", async () => {
    const { log, env } = stubDocument(true);
    env.navigator = {};
    await expect(copyText("the answer", env)).resolves.toBe(true);
    expect(log).toEqual(["append", "select:the answer", "exec:copy", "remove"]);
  });

  it("uses navigator.clipboard where it exists, and the fallback when it refuses", async () => {
    const written: string[] = [];
    const { log, env } = stubDocument(true);
    env.navigator = { clipboard: { writeText: async (text: string) => void written.push(text) } };
    await expect(copyText("a", env)).resolves.toBe(true);
    expect(written).toEqual(["a"]);
    expect(log).toEqual([]);
    env.navigator = { clipboard: { writeText: async () => Promise.reject(new Error("denied")) } };
    await expect(copyText("b", env)).resolves.toBe(true);
    expect(log).toContain("exec:copy");
  });

  it("says so when neither way copies", async () => {
    const { env } = stubDocument(false);
    await expect(copyText("x", env)).resolves.toBe(false);
  });
});
