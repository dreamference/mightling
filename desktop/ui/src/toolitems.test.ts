// Tool calls in full (DESKTOP §16 B11).
import { describe, expect, it } from "vitest";

import type { ThreadItem } from "./protocol/v2/ThreadItem";
import { jsonText, mcpContent, PART_CAP, took, toolView } from "./toolitems";
import { initialState, reduce, type Action } from "./store";
import type { ServerNotification } from "./protocol/ServerNotification";
import type { Thread } from "./protocol/v2/Thread";

const item = (value: object) => value as unknown as ThreadItem;

describe("tool calls in full", () => {
  it("show an MCP call's arguments, progress, result and how long it took", () => {
    const running = toolView(item({ type: "mcpToolCall", id: "m", server: "tickets", tool: "create", status: "inProgress", arguments: { title: "Retry" }, result: null, error: null, durationMs: null }), ["Looking up the project", "Creating"]);
    expect(running).toMatchObject({ label: "tickets · create", state: "Creating", failed: false });
    expect(running?.parts.map((p) => p.title)).toEqual(["Arguments", "Progress"]);
    const done = toolView(item({ type: "mcpToolCall", id: "m", server: "tickets", tool: "create", status: "completed", arguments: {}, error: null, durationMs: 2400,
      result: { content: [{ type: "text", text: "Created OPS-12" }, { type: "image", mimeType: "image/png", data: "…" }], structuredContent: { key: "OPS-12" }, _meta: null } }));
    expect(done?.state).toBe("2.4 s");
    expect(done?.parts).toEqual([{ title: "Result", text: 'Created OPS-12\n\n[an image, image/png]\n\n{\n  "key": "OPS-12"\n}' }]);
    const failed = toolView(item({ type: "mcpToolCall", id: "m", server: "s", tool: "t", status: "failed", arguments: null, result: null, error: { message: "timed out" }, durationMs: 30000 }));
    expect(failed).toMatchObject({ state: "failed", failed: true, parts: [{ title: "Error", text: "timed out" }] });
  });

  it("name the other tools in words", () => {
    expect(toolView(item({ type: "dynamicToolCall", id: "d", namespace: "ext", tool: "lookup", arguments: { q: "x" }, status: "completed", contentItems: [{ type: "inputText", text: "found" }], success: true }))).toMatchObject({ label: "ext · lookup", parts: [{ title: "Arguments" }, { title: "Result", text: "found" }] });
    expect(toolView(item({ type: "dynamicToolCall", id: "d", namespace: null, tool: "mightling_terminal_output", arguments: { lines: 5 }, status: "completed", contentItems: [{ type: "inputText", text: "$ ls" }], success: true }))).toMatchObject({ label: "Read the terminal", parts: [{ title: "What it read", text: "$ ls" }] });
    expect(toolView(item({ type: "imageView", id: "i", path: "/w/shot.png" }))).toMatchObject({ label: "Looked at an image", parts: [{ title: "File", text: "/w/shot.png" }] });
    expect(toolView(item({ type: "hookPrompt", id: "h", fragments: [{ text: "Run the linter first.", hookRunId: "r" }] }))?.label).toBe("A hook added to the conversation");
    expect(toolView(item({ type: "sleep", id: "s", durationMs: 90_000 }))).toMatchObject({ label: "Waited", state: "2 min", parts: [] });
    expect(toolView(item({ type: "webSearch", id: "w", query: "nvfp4", action: { type: "search", query: "nvfp4", queries: null }, results: null }))?.label).toBe("Searched the web: nvfp4");
    expect(toolView(item({ type: "webSearch", id: "w", query: "", action: { type: "openPage", url: "https://example.org/a" }, results: null }))?.label).toBe("Opened https://example.org/a");
    expect(toolView(item({ type: "functionCallOutput", id: "f", name: "apply", namespace: null, output: [{ type: "input_text", text: "ok" }] }))).toMatchObject({ label: "apply", parts: [{ title: "Output", text: "ok" }] });
    expect(toolView(item({ type: "agentMessage", id: "a", text: "hi" }))).toBeNull();
  });

  it("keep each MCP call's progress on its turn", () => {
    const thread = { id: "t", cwd: "/w", updatedAt: 1, createdAt: 1, ephemeral: false, parentThreadId: null, preview: "", name: null, turns: [], status: { type: "idle" } } as unknown as Thread;
    const note = (method: string, params: object): Action => ({ type: "notification", notification: { method, params } as unknown as ServerNotification });
    const state = [
      { type: "opened", thread } as Action,
      note("item/mcpToolCall/progress", { threadId: "t", turnId: "u", itemId: "m", message: "one" }),
      note("item/mcpToolCall/progress", { threadId: "t", turnId: "u", itemId: "m", message: "two" }),
    ].reduce(reduce, initialState);
    expect(state.threads.t.turns[0].progress).toEqual({ m: ["one", "two"] });
  });

  it("write JSON, durations and long parts readably", () => {
    expect(jsonText({})).toBeNull();
    expect(jsonText("plain")).toBe("plain");
    expect(took(850)).toBe("850 ms");
    // Rounded first: 999.6 ms is a second, not "1000 ms".
    expect(took(999.6)).toBe("1.0 s");
    expect(took(59_999)).toBe("1 min");
    expect(took(null)).toBeNull();
    expect(mcpContent([{ type: "resource", resource: { uri: "file:///a" } }])).toBe("[resource file:///a]");
    const long = toolView(item({ type: "functionCallOutput", id: "f", name: "x", namespace: null, output: "y".repeat(PART_CAP + 10) }));
    expect(long?.parts[0].text).toContain("10 more characters");
  });
});
