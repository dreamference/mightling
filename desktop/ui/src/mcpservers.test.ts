// MCP servers in Skills and tools (DESKTOP §16 B34): the Add server form's fields as config.toml
// keys them, what it refuses (a token in clear above all), Remove and the startup status.
import { describe, expect, it } from "vitest";

import { bearerVariable, formProblem, parseLines, ENV_NAME, removable, serverEnabled, serverForm, serverValue, startupLabel } from "./mcpservers";

const free = () => false;

describe("MCP servers", () => {
  it("write a command with only the fields filled: arguments, environment, working folder", () => {
    expect(serverValue(serverForm({ name: "docs", target: " ling-docs " }))).toEqual({ command: "ling-docs" });
    const form = serverForm({ name: "docs", target: "node", args: "server.js  --port 8080", env: "# comment\nPORT=8080\n\nURL = http://a:1/b?c=d", cwd: " /srv/app " });
    expect(formProblem(form, free)).toBeNull();
    expect(serverValue(form)).toEqual({ command: "node", args: ["server.js", "--port", "8080"], env: { PORT: "8080", URL: "http://a:1/b?c=d" }, cwd: "/srv/app" });
  });

  it("write a URL with its headers and the bearer token's variable, by name only", () => {
    const form = serverForm({ name: "remote", kind: "url", target: "https://mcp.example/mcp", headers: "X-Team: core\nX-Trace: on", bearer: "${MCP_TOKEN}" });
    expect(formProblem(form, free)).toBeNull();
    expect(serverValue(form)).toEqual({ url: "https://mcp.example/mcp", http_headers: { "X-Team": "core", "X-Trace": "on" }, bearer_token_env_var: "MCP_TOKEN" });
    // The command's fields are not written for a URL, nor the URL's for a command.
    expect(serverValue({ ...form, kind: "command", env: "A=1" })).toEqual({ command: "https://mcp.example/mcp", env: { A: "1" } });
  });

  it("never take a token where its variable's name goes, nor an Authorization header", () => {
    expect(bearerVariable("MCP_TOKEN")).toBe("MCP_TOKEN");
    expect(bearerVariable("$MCP_TOKEN")).toBe("MCP_TOKEN");
    expect(bearerVariable("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc")).toBeNull();
    expect(bearerVariable("ghp_abc-123")).toBeNull();
    const url = serverForm({ name: "remote", kind: "url", target: "https://mcp.example/mcp" });
    expect(formProblem({ ...url, bearer: "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc" }, free)).toMatch(/not the token/);
    expect(formProblem({ ...url, headers: "authorization: Bearer abc" }, free)).toMatch(/No Authorization header/);
  });

  it("say what is missing or wrong before Add is offered", () => {
    expect(formProblem(serverForm({ name: "bad name", target: "x" }), free)).toMatch(/A name/);
    expect(formProblem(serverForm({ name: "docs", target: "x" }), (name) => name === "docs")).toMatch(/in config.toml already/);
    expect(formProblem(serverForm({ name: "docs", target: " " }), free)).toBe("The command to run.");
    expect(formProblem(serverForm({ name: "docs", target: "x", env: "PORT 8080" }), free)).toBe("Not NAME=value: PORT 8080");
    expect(formProblem(serverForm({ name: "docs", target: "x", env: "1PORT=8080" }), free)).toMatch(/Not NAME=value/);
    expect(formProblem(serverForm({ name: "docs", target: "x", cwd: "app" }), free)).toMatch(/full path/);
    expect(formProblem(serverForm({ name: "docs", target: "x", cwd: "C:\\app" }), free)).toBeNull();
    expect(formProblem(serverForm({ name: "r", kind: "url", target: "ftp://mcp.example/mcp" }), free)).toMatch(/http/);
    expect(formProblem(serverForm({ name: "r", kind: "url", target: "https://x/mcp", headers: "no colon" }), free)).toMatch(/Not Name: value/);
  });

  it("parse lines by their separator and name rule", () => {
    expect(parseLines("A=1=2\nB=", "=", ENV_NAME)).toEqual({ values: { A: "1=2", B: "" }, bad: [] });
    expect(parseLines("=x\nC", "=", ENV_NAME)).toEqual({ values: {}, bad: ["=x", "C"] });
  });

  it("offer Remove for the user's own servers only: not a plugin's, not a bundle's, not one config.toml lacks", () => {
    const config = { mcp_servers: { docs: { command: "ling-docs" }, notes: { command: "/b/notes" } } };
    expect(removable({ name: "docs" }, config, ["notes"])).toBe(true);
    expect(removable({ name: "notes" }, config, ["notes"])).toBe(false);
    expect(removable({ name: "docs", pluginId: "lint@team" }, config, [])).toBe(false);
    expect(removable({ name: "ling-code" }, config, [])).toBe(false);
  });

  it("give the startup status in words: off, then a thread's report, then the listing's", () => {
    const listed = { runtimeStatus: null, toolsError: null };
    expect(serverEnabled({ mcp_servers: { a: { enabled: false } } }, "a")).toBe(false);
    expect(serverEnabled({ mcp_servers: { a: {} } }, "a")).toBe(true);
    expect(serverEnabled(null, "a")).toBe(true);
    expect(startupLabel(listed, false, { status: "ready", error: null })).toBe("off");
    expect(startupLabel(listed, true, { status: "failed", error: "exited with status 1" })).toBe("failed to start: exited with status 1");
    expect(startupLabel(listed, true, { status: "ready", error: null })).toBe("running");
    expect(startupLabel(listed, true, { status: "starting", error: null })).toBe("starting");
    expect(startupLabel({ runtimeStatus: "authenticationRequired", toolsError: null }, true)).toBe("needs sign-in");
    expect(startupLabel({ runtimeStatus: null, toolsError: "connection refused" }, true)).toBe("failed to start");
    expect(startupLabel(listed, true)).toBe("starts");
  });
});
