// Recommended MCP servers (DESKTOP §16 B26): what is offered, and never at airgapped on.
import { describe, expect, it } from "vitest";

import { MCP_NAME } from "./ExtensionsView";
import { RECOMMENDED_SERVERS, offeredServers } from "./mcprecommended";

describe("recommended MCP servers", () => {
  it("are a short list of valid names, each started by a package runner and marked as needing the network", () => {
    expect(RECOMMENDED_SERVERS.length).toBeLessThanOrEqual(8);
    for (const server of RECOMMENDED_SERVERS) {
      expect(server.name).toMatch(MCP_NAME);
      expect(["npx", "uvx"]).toContain(server.command);
      expect(server.network).not.toBe("");
    }
  });

  it("offers none while airgapped is on, and none already configured under its name", () => {
    expect(offeredServers(true, [])).toEqual([]);
    expect(offeredServers(false, ["git"]).map((server) => server.name)).not.toContain("git");
    expect(offeredServers(false, []).length).toBe(RECOMMENDED_SERVERS.length);
  });
});
