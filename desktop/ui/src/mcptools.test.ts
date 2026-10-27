// Rules per MCP tool (DESKTOP §16 B16).
import { describe, expect, it } from "vitest";

import { ruleEdits, toolRules } from "./mcptools";

const config = { mcp_servers: { tickets: { command: "x", disabled_tools: ["delete_ticket"], tools: { create_ticket: { approval_mode: "approve" }, close_ticket: { approval_mode: "prompt" } } } } };

describe("rules per MCP tool", () => {
  it("are read from the config, a turned-off tool listed though the server no longer offers it", () => {
    expect(toolRules(config, "tickets", ["create_ticket", "close_ticket", "list_tickets"])).toEqual([
      { tool: "close_ticket", rule: "prompt", editable: true },
      { tool: "create_ticket", rule: "approve", editable: true },
      { tool: "delete_ticket", rule: "off", editable: true },
      { tool: "list_tickets", rule: "default", editable: true },
    ]);
    expect(toolRules({}, "other", ["a.b"])).toEqual([{ tool: "a.b", rule: "default", editable: false }]);
  });

  it("are written as the keys ling reads, one batch per change", () => {
    expect(ruleEdits(config, "tickets", "list_tickets", "approve")).toEqual([{ keyPath: "mcp_servers.tickets.tools.list_tickets.approval_mode", value: "approve", mergeStrategy: "replace" }]);
    expect(ruleEdits(config, "tickets", "create_ticket", "default")).toEqual([{ keyPath: "mcp_servers.tickets.tools.create_ticket.approval_mode", value: null, mergeStrategy: "replace" }]);
    expect(ruleEdits(config, "tickets", "list_tickets", "off")).toEqual([
      { keyPath: "mcp_servers.tickets.tools.list_tickets.approval_mode", value: null, mergeStrategy: "replace" },
      { keyPath: "mcp_servers.tickets.disabled_tools", value: ["delete_ticket", "list_tickets"], mergeStrategy: "replace" },
    ]);
    expect(ruleEdits(config, "tickets", "delete_ticket", "prompt")[1]).toEqual({ keyPath: "mcp_servers.tickets.disabled_tools", value: null, mergeStrategy: "replace" });
    expect(() => ruleEdits(config, "tickets", "a.b", "off")).toThrow("cannot be written");
  });
});
