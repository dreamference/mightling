// Plugins in Skills and tools (DESKTOP §16 B34): the search, the Installed row, on/off and the
// details read before an install.
import { describe, expect, it } from "vitest";

import { installedPlugins, pluginEnabledEdit, pluginFacts, pluginMatches, searchMarketplaces } from "./plugins";
import type { PluginDetail } from "./protocol/v2/PluginDetail";
import type { PluginMarketplaceEntry } from "./protocol/v2/PluginMarketplaceEntry";
import type { PluginSummary } from "./protocol/v2/PluginSummary";

const plugin = (id: string, fill: Partial<PluginSummary> = {}): PluginSummary =>
  ({ id, name: id.split("@")[0], version: null, localVersion: null, installed: false, enabled: false, keywords: [], ...fill }) as PluginSummary;
const market = (name: string, plugins: PluginSummary[], path: string | null = `/srv/${name}/marketplace.json`): PluginMarketplaceEntry => ({ name, path, interface: null, plugins });

describe("plugins", () => {
  it("match every word of the search in the name, the id or a keyword, case ignored", () => {
    const lint = plugin("lint@team", { keywords: ["Quality", "checks"] });
    expect(pluginMatches(lint, "")).toBe(true);
    expect(pluginMatches(lint, "LINT")).toBe(true);
    expect(pluginMatches(lint, "team quality")).toBe(true);
    expect(pluginMatches(lint, "lint deploy")).toBe(false);
  });

  it("leave out a marketplace with nothing matching while searching, and keep every one without a search", () => {
    const markets = [market("team", [plugin("lint@team"), plugin("format@team")]), market("ops", [plugin("deploy@ops")])];
    expect(searchMarketplaces(markets, "  ")).toBe(markets);
    expect(searchMarketplaces(markets, "format")).toEqual([market("team", [plugin("format@team")])]);
    expect(searchMarketplaces(markets, "nothing")).toEqual([]);
  });

  it("list each installed plugin once, with the first marketplace that lists it", () => {
    const shared = plugin("lint@team", { installed: true, enabled: true });
    const installed = installedPlugins([market("team", [shared, plugin("format@team")]), market("mirror", [shared])]);
    expect(installed).toEqual([{ plugin: shared, market: "team" }]);
  });

  it("turn a plugin off or on as plugins.<id>.enabled, merged so an id with a dot needs no quoting", () => {
    expect(pluginEnabledEdit("lint@team.tools", false)).toEqual({
      edits: [{ keyPath: "plugins", value: { "lint@team.tools": { enabled: false } }, mergeStrategy: "upsert" }],
      filePath: null, expectedVersion: null, reloadUserConfig: true,
    });
  });

  it("show what plugin/read says the plugin adds before it is installed", () => {
    const detail = {
      marketplaceName: "team", marketplacePath: "/srv/team/marketplace.json", summary: plugin("lint@team", { version: "1.2.0" }), shareUrl: null,
      description: "Runs checks.", onboardingSkill: null, appTemplates: [], scheduledTasks: null,
      skills: [{ name: "lint", description: "Lints files", shortDescription: null, interface: null, path: null, enabled: true }, { name: "fix", description: "", shortDescription: null, interface: null, path: null, enabled: true }],
      hooks: [{ key: "a", eventName: "postToolUse" }, { key: "b", eventName: "postToolUse" }],
      apps: [{ id: "x", name: "Tracker", description: null, installUrl: null, category: null }],
      mcpServers: ["lint-server"],
    } as PluginDetail;
    expect(pluginFacts(detail)).toEqual({
      title: "lint 1.2.0", description: "Runs checks.", skills: ["$lint: Lints files", "$fix"], mcpServers: ["lint-server"], hooks: ["postToolUse"], apps: ["Tracker"],
    });
    expect(pluginFacts({ ...detail, summary: plugin("lint@team", { localVersion: "0.9" }) }).title).toBe("lint 0.9");
  });
});
