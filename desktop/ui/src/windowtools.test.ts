// The window's own tools (the owner's decision 5): bounded, secrets masked, and in words in the transcript.
import { describe, expect, it } from "vitest";

import { CONSOLE_TOOL, MAX_ANSWER, TERMINAL_TOOL, bounded, consoleAnswer, linesAsked, maskSecrets, terminalAnswer, toolLabel, windowToolNames } from "./windowtools";

describe("the window's tools", () => {
  it("are both in the desktop app, the terminal's alone in a browser", () => {
    expect(windowToolNames(true)).toEqual([TERMINAL_TOOL, CONSOLE_TOOL]);
    expect(windowToolNames(false)).toEqual([TERMINAL_TOOL]);
    expect(toolLabel(TERMINAL_TOOL)).toBe("Read the terminal");
    expect(toolLabel("other")).toBeNull();
  });

  it("mask what looks like a secret and leave ordinary output alone", () => {
    const text = [
      "export OPENAI_API_KEY=sk-proj-abcdefghijklmnop1234",
      "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWX12",
      "Authorization: Bearer abcdef0123456789xyz",
      "aws AKIAABCDEFGHIJKLMNOP",
      "jwt eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4",
      "password = 'hunter22'",
      "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
      "  ✓ 42 tests passed in 1.2s",
    ].join("\n");
    const masked = maskSecrets(text);
    for (const secret of ["sk-proj-abcdefghijklmnop1234", "ghp_ABCDEFGHIJKLMNOPQRSTUVWX12", "abcdef0123456789xyz", "AKIAABCDEFGHIJKLMNOP", "eyJhbGciOiJI", "hunter22", "MIIabc"]) {
      expect(masked).not.toContain(secret);
    }
    expect(masked).toContain("OPENAI_API_KEY=[masked]");
    expect(masked).toContain("✓ 42 tests passed in 1.2s");
  });

  it("answer with each tab's last lines, within bounds, newest kept when cut", () => {
    expect(linesAsked({ lines: 5 })).toBe(5);
    expect(linesAsked({ lines: 10_000 })).toBe(400);
    expect(linesAsked({})).toBe(120);
    expect(linesAsked({ lines: "x" })).toBe(120);
    const output = Array.from({ length: 50 }, (_, index) => `line ${index + 1}`).join("\n");
    const answer = terminalAnswer([{ title: "npm run dev", cwd: "/w/app", running: true, output }], 3);
    expect(answer).toContain('## Terminal "npm run dev" in /w/app (running)');
    expect(answer).toContain("line 48\nline 49\nline 50");
    expect(answer).not.toContain("line 47");
    expect(terminalAnswer([], 3)).toMatch(/No terminal tab/);
    expect(consoleAnswer([{ url: "http://127.0.0.1:5173/", lines: ["[error] Uncaught TypeError: x is undefined (main.js:12)"] }], 10)).toContain("[error] Uncaught TypeError");
    const huge = bounded("x".repeat(MAX_ANSWER + 100) + "END");
    expect(huge.startsWith("[earlier output cut]")).toBe(true);
    expect(huge.endsWith("END")).toBe(true);
  });
});
