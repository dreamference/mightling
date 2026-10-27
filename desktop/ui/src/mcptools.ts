// Rules per MCP tool (DESKTOP_PARITY_CLAUDE gap 8, DESKTOP §16 B16), in the keys the pinned `ling`
// reads (Codex's `McpServerToolConfig`): `mcp_servers.<server>.tools.<tool>.approval_mode` is
// `approve` (always allowed) or `prompt` (asks each time), unset for the server's default; a tool
// turned off is in `mcp_servers.<server>.disabled_tools`, which removes it from the agent's tools.
// Pure: no DOM.

import type { ConfigEdit } from "./protocol/v2/ConfigEdit";

export type ToolRule = "default" | "approve" | "prompt" | "off";

export const RULE_LABELS: Record<ToolRule, string> = { default: "As the server says", approve: "Always allow", prompt: "Ask each time", off: "Off" };

/** A key path segment the config writer takes: no dots, nothing it would split or quote. */
export const KEY_SEGMENT = /^[A-Za-z0-9_-]{1,128}$/;

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

/** A server's entry in the config `config/read` answered, or an empty one. */
function serverConfig(config: unknown, server: string): Json {
  const servers = isObject(config) && isObject(config.mcp_servers) ? config.mcp_servers : {};
  return isObject(servers[server]) ? (servers[server] as Json) : {};
}

/** The tools turned off for a server. */
export function disabledTools(config: unknown, server: string): string[] {
  const list = serverConfig(config, server).disabled_tools;
  return Array.isArray(list) ? list.filter((entry): entry is string => typeof entry === "string") : [];
}

/** Each tool of a server with its rule: the tools it lists now, and the ones turned off (which it no longer lists). */
export function toolRules(config: unknown, server: string, listed: string[]): { tool: string; rule: ToolRule; editable: boolean }[] {
  const off = disabledTools(config, server);
  const tools = isObject(serverConfig(config, server).tools) ? (serverConfig(config, server).tools as Json) : {};
  const names = [...new Set([...listed, ...off])].sort((a, b) => a.localeCompare(b));
  return names.map((tool) => {
    const mode = isObject(tools[tool]) ? (tools[tool] as Json).approval_mode : undefined;
    const rule: ToolRule = off.includes(tool) ? "off" : mode === "approve" ? "approve" : mode === "prompt" ? "prompt" : "default";
    return { tool, rule, editable: KEY_SEGMENT.test(server) && KEY_SEGMENT.test(tool) };
  });
}

/** The config edits that give one tool its rule, as one batch. */
export function ruleEdits(config: unknown, server: string, tool: string, rule: ToolRule): ConfigEdit[] {
  if (!KEY_SEGMENT.test(server) || !KEY_SEGMENT.test(tool)) throw new Error("this tool's name cannot be written as a setting");
  const off = disabledTools(config, server);
  const nextOff = rule === "off" ? [...new Set([...off, tool])] : off.filter((entry) => entry !== tool);
  const edits: ConfigEdit[] = [
    { keyPath: `mcp_servers.${server}.tools.${tool}.approval_mode`, value: rule === "approve" || rule === "prompt" ? rule : null, mergeStrategy: "replace" },
  ];
  if (nextOff.length !== off.length) edits.push({ keyPath: `mcp_servers.${server}.disabled_tools`, value: nextOff.length ? nextOff : null, mergeStrategy: "replace" });
  return edits;
}
