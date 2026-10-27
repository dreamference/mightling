// The Add server form, Remove and the startup status in Skills and tools › MCP servers
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B34), in the keys of Codex's `mcp_servers.<name>`:
// a command (`command`, `args`, `env`, `cwd`) or a URL (`url`, `http_headers`,
// `bearer_token_env_var`). A bearer token is never written: only the name of the environment
// variable that holds it. Pure: no DOM.

import type { McpServerStartupState } from "./protocol/v2/McpServerStartupState";

/** What the form holds; `target` is the command or the URL, as the form had it before B34. */
export interface McpServerForm {
  name: string;
  kind: "command" | "url";
  target: string;
  args: string;
  env: string;
  cwd: string;
  headers: string;
  bearer: string;
}

/** An empty form, or one filled in (a recommended server, B26). */
export function serverForm(fill: Partial<McpServerForm> = {}): McpServerForm {
  return { name: "", kind: "command", target: "", args: "", env: "", cwd: "", headers: "", bearer: "", ...fill };
}

/** An MCP server's name as config.toml keys it. */
export const MCP_NAME = /^[A-Za-z0-9_-]{1,64}$/;

/** An environment variable's name. */
export const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** An HTTP header's name (RFC 9110 token). */
const HEADER_NAME = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/;

/**
 * `NAME=value` (environment) or `Name: value` (headers) lines, blank lines and `#` comments skipped;
 * the lines that are not of that form, as typed, in `bad`.
 */
export function parseLines(text: string, separator: "=" | ":", name: RegExp): { values: Record<string, string>; bad: string[] } {
  const values: Record<string, string> = {};
  const bad: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const at = line.indexOf(separator);
    const key = at > 0 ? line.slice(0, at).trim() : "";
    if (!name.test(key)) bad.push(line);
    else values[key] = line.slice(at + 1).trim();
  }
  return { values, bad };
}

/** The variable's name as typed, a `$NAME` or `${NAME}` taken as NAME; null when it is not a name (a pasted token is not). */
export function bearerVariable(text: string): string | null {
  const typed = text.trim();
  const name = typed.startsWith("${") && typed.endsWith("}") ? typed.slice(2, -1) : typed.startsWith("$") ? typed.slice(1) : typed;
  return ENV_NAME.test(name) ? name : null;
}

/** Whether a folder is absolute (POSIX, or a Windows drive). */
function absolute(folder: string): boolean {
  return folder.startsWith("/") || /^[A-Za-z]:[\\/]/.test(folder);
}

/** Why the form cannot be added yet, in words; null when it can. `taken` names the servers config.toml has. */
export function formProblem(form: McpServerForm, taken: (name: string) => boolean): string | null {
  if (!MCP_NAME.test(form.name)) return "A name of letters, digits, - and _.";
  if (taken(form.name)) return `A server named ${form.name} is in config.toml already: remove it first.`;
  const target = form.target.trim();
  if (form.kind === "command") {
    if (!target) return "The command to run.";
    const env = parseLines(form.env, "=", ENV_NAME);
    if (env.bad.length) return `Not NAME=value: ${env.bad[0]}`;
    if (form.cwd.trim() && !absolute(form.cwd.trim())) return "The working folder as a full path.";
    return null;
  }
  let url: URL | null = null;
  try {
    url = new URL(target);
  } catch {
    url = null;
  }
  if (!url || (url.protocol !== "http:" && url.protocol !== "https:")) return "An http:// or https:// address.";
  const headers = parseLines(form.headers, ":", HEADER_NAME);
  if (headers.bad.length) return `Not Name: value: ${headers.bad[0]}`;
  if (Object.keys(headers.values).some((name) => name.toLowerCase() === "authorization")) {
    return "No Authorization header: it would keep the token in config.toml. Name the variable that holds the token under Bearer-token variable.";
  }
  if (form.bearer.trim() && bearerVariable(form.bearer) === null) return "The bearer-token variable's name (e.g. MCP_TOKEN), not the token.";
  return null;
}

/** The config.toml value of a new server: only the fields filled in. Call when `formProblem` is null. */
export function serverValue(form: McpServerForm): Record<string, unknown> {
  if (form.kind === "url") {
    const value: Record<string, unknown> = { url: form.target.trim() };
    const headers = parseLines(form.headers, ":", HEADER_NAME).values;
    if (Object.keys(headers).length) value.http_headers = headers;
    const bearer = bearerVariable(form.bearer);
    if (bearer) value.bearer_token_env_var = bearer;
    return value;
  }
  const value: Record<string, unknown> = { command: form.target.trim() };
  const args = form.args.trim();
  if (args) value.args = args.split(/\s+/);
  const env = parseLines(form.env, "=", ENV_NAME).values;
  if (Object.keys(env).length) value.env = env;
  if (form.cwd.trim()) value.cwd = form.cwd.trim();
  return value;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

/** A server's entry in the config `config/read` answered, or null. */
function entry(config: unknown, name: string): Json | null {
  const servers = isObject(config) && isObject(config.mcp_servers) ? config.mcp_servers : null;
  return servers && Object.prototype.hasOwnProperty.call(servers, name) && isObject(servers[name]) ? servers[name] : null;
}

/** Whether config.toml has this server under `mcp_servers` (a plugin's server is not there). */
export function configured(config: unknown, name: string): boolean {
  return entry(config, name) !== null;
}

/** Whether a server is on: `enabled` is not false. */
export function serverEnabled(config: unknown, name: string): boolean {
  return entry(config, name)?.enabled !== false;
}

/** Whether Remove is offered: the server is the user's own in config.toml, not a plugin's, not a bundle's (B45's Remove also deletes its folder). */
export function removable(server: { name: string; pluginId?: string | null }, config: unknown, bundles: string[]): boolean {
  return !server.pluginId && configured(config, server.name) && !bundles.includes(server.name);
}

/** The last startup a thread reported for a server (`mcpServer/startupStatus/updated`). */
export interface Startup {
  status: McpServerStartupState;
  error: string | null;
}

const RUNTIME: Record<string, string> = {
  notStarted: "not started",
  starting: "starting",
  connected: "running",
  authenticationRequired: "needs sign-in",
  failed: "failed to start",
  cancelled: "start cancelled",
  disabled: "off",
};

/**
 * A server's startup status, in words: off when config.toml turns it off; else what a thread last
 * reported while the window was open; else the status `mcpServerStatus/list` gave (a thread's, null
 * without one); else whether it started for the tool listing, which starts each server.
 */
export function startupLabel(server: { runtimeStatus: string | null; toolsError: string | null }, enabled: boolean, startup?: Startup): string {
  if (!enabled) return "off";
  if (startup) {
    if (startup.status === "ready") return "running";
    if (startup.status === "failed") return startup.error ? `failed to start: ${startup.error}` : "failed to start";
    return RUNTIME[startup.status] ?? startup.status;
  }
  if (server.runtimeStatus) return RUNTIME[server.runtimeStatus] ?? server.runtimeStatus;
  return server.toolsError ? "failed to start" : "starts";
}
