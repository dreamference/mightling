// Terminal tabs (DESKTOP_PARITY §2.7) against a scripted app-server stand-in: a tab is a PTY the
// server runs with command/exec under the composer's permissions, its keystrokes and size go back,
// its output reaches the tab (also what came before the tab was drawn), Close terminates it, and
// Send to the thread gives the agent the recent output without the terminal's control sequences.
import { describe, expect, it } from "vitest";

import { ScriptedServer } from "./scripted-server";
import { TerminalHub, decode, encode, shellCommand, terminalMessage } from "./terminal";

describe("a terminal tab", () => {
  it("runs a login shell under the thread's permission profile and echoes what is typed", async () => {
    let finish: (code: number) => void = () => {};
    const server = new ScriptedServer({
      "command/exec": () => new Promise((resolve) => { finish = (code) => resolve({ exitCode: code, stdout: "", stderr: "" }); }),
      "command/exec/write": (params, self) => {
        // The PTY echoes the line and the shell answers.
        const typed = new TextDecoder().decode(decode(params.deltaBase64 as string));
        self.notify("command/exec/outputDelta", { processId: params.processId, stream: "stdout", deltaBase64: encode(`${typed.replace(/\r/g, "\r\n")}\x1b[32mhello\x1b[0m\r\n$ `), capReached: false });
        return {};
      },
      "command/exec/resize": () => ({}),
      "command/exec/terminate": () => {
        finish(130);
        return {};
      },
    });
    // The stand-in answers command/exec only when the process ends, as the server does.
    server.client.onNotification = (notification) => hub.deliver(notification);
    const hub = new TerminalHub(server.client);
    const exits: [string, number, string | null][] = [];
    const session = hub.start({ threadId: "t", cwd: "/w/app", profile: ":workspace", shell: null, size: { rows: 24, cols: 100 } }, (id, code, error) => exits.push([id, code, error]));
    expect(session).toMatchObject({ threadId: "t", cwd: "/w/app", title: "Terminal", exitCode: null });
    expect(server.sent[0]).toEqual({ method: "command/exec", params: {
      command: ["/bin/sh", "-c", 'exec "${SHELL:-/bin/bash}" -l'], processId: session.processId, tty: true, disableTimeout: true, disableOutputCap: true,
      cwd: "/w/app", size: { rows: 24, cols: 100 }, permissionProfile: ":workspace" } });

    // Output that arrives before the tab is drawn is kept for it.
    await hub.write(session.processId, "echo hello\r");
    await new Promise((resolve) => setTimeout(resolve, 5));
    const seen: string[] = [];
    const detach = hub.attach(session.processId, (bytes) => seen.push(new TextDecoder().decode(bytes)));
    expect(seen.join("")).toContain("echo hello");
    expect(server.sent[1]).toEqual({ method: "command/exec/write", params: { processId: session.processId, deltaBase64: encode("echo hello\r") } });

    await hub.resize(session.processId, 40, 120);
    expect(server.sent[2]).toEqual({ method: "command/exec/resize", params: { processId: session.processId, size: { rows: 40, cols: 120 } } });
    expect(hub.tail(session.processId)).toBe("echo hello\nhello\n$");
    expect(terminalMessage("/w/app", "hello")).toBe("Here is what my terminal in /w/app shows:\n\n```\nhello\n```");

    await hub.terminate(session.processId);
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(exits).toEqual([[session.processId, 130, null]]);
    detach();
  });

  it("runs an environment action as a script, or the configured shell", () => {
    expect(shellCommand("/usr/bin/fish")).toEqual(["/usr/bin/fish", "-l"]);
    expect(shellCommand("/usr/bin/fish", "npm start")).toEqual(["/usr/bin/fish", "-l", "-c", "npm start"]);
    expect(shellCommand(null, "npm start")).toEqual(["/bin/sh", "-c", 'exec "${SHELL:-/bin/bash}" -l -c "$0"', "npm start"]);
  });

  it("reports a refused command/exec as the tab's error", async () => {
    const server = new ScriptedServer({});
    const hub = new TerminalHub(server.client);
    const exits: [number, string | null][] = [];
    hub.start({ threadId: null, cwd: "/w", profile: null, shell: null, size: { rows: 24, cols: 80 } }, (_id, code, error) => exits.push([code, error]));
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(exits).toEqual([[-1, "not scripted: command/exec"]]);
    expect(server.sent[0].params).not.toHaveProperty("permissionProfile");
  });
});
