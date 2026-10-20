// Skills and tools (DESKTOP_PARITY §2.11): where a skill comes from, and the config.toml value of a
// new MCP server.
import { describe, expect, it } from "vitest";

import { MCP_NAME, hookEnabledEdit, hookNeedsReview, hookTrustEdit, hookTrustLabel, mcpServerValue, skillSource, uniqueHooks } from "./ExtensionsView";

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

  it("trust a hook as the TUI does, and say which ones Codex skips until then (item 15)", () => {
    expect(hookNeedsReview({ trustStatus: "untrusted" })).toBe(true);
    expect(hookNeedsReview({ trustStatus: "modified" })).toBe(true);
    expect(hookNeedsReview({ trustStatus: "trusted" })).toBe(false);
    expect(hookNeedsReview({ trustStatus: "managed" })).toBe(false);
    expect(hookTrustLabel({ trustStatus: "modified" })).toBe("changed since it was trusted: skipped");
    expect(hookTrustEdit([{ key: "a:stop:0:0", currentHash: "h1" }, { key: "b:stop:0:0", currentHash: "h2" }])).toEqual({
      edits: [{ keyPath: "hooks.state", value: { "a:stop:0:0": { trusted_hash: "h1" }, "b:stop:0:0": { trusted_hash: "h2" } }, mergeStrategy: "upsert" }],
      filePath: null, expectedVersion: null, reloadUserConfig: true,
    });
    expect(hookEnabledEdit("a:stop:0:0", false).edits).toEqual([{ keyPath: "hooks.state", value: { "a:stop:0:0": { enabled: false } }, mergeStrategy: "upsert" }]);
    // A user's hook is listed once, however many folders were asked about.
    const hook = { key: "u", trustStatus: "trusted" } as never;
    expect(uniqueHooks([{ cwd: "/a", hooks: [hook], warnings: [], errors: [] }, { cwd: "/b", hooks: [hook], warnings: [], errors: [] }])).toHaveLength(1);
  });
});
