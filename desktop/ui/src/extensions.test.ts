// Skills and tools (DESKTOP_PARITY §2.11): where a skill comes from, and the config.toml value of a
// new MCP server.
import { describe, expect, it } from "vitest";

import { MCP_NAME, mcpServerValue, skillSource } from "./ExtensionsView";

describe("skills and tools", () => {
  it("say where a skill comes from, another agent's linked in included", () => {
    expect(skillSource({ scope: "user", path: "/home/u/.mightling/skills/from-otheragent/pdf/SKILL.md" } as never)).toBe("from otheragent");
    expect(skillSource({ scope: "repo", path: "/w/app/.mightling/skills/release/SKILL.md" } as never)).toBe("this project's");
    expect(skillSource({ scope: "system", path: "/x" } as never)).toBe("built in");
  });

  it("write a new MCP server as a command with arguments or a URL, under a plain name", () => {
    expect(mcpServerValue("command", " npx ", "-y  @acme/mcp  --stdio ")).toEqual({ command: "npx", args: ["-y", "@acme/mcp", "--stdio"] });
    expect(mcpServerValue("url", " http://127.0.0.1:8765/mcp ", "")).toEqual({ url: "http://127.0.0.1:8765/mcp" });
    expect(MCP_NAME.test("ling-code")).toBe(true);
    expect(MCP_NAME.test("bad name")).toBe(false);
    expect(MCP_NAME.test("a.b")).toBe(false);
  });
});
