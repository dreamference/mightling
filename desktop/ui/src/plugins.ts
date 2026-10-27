// Plugins in Skills and tools (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16 B34): the search over
// `plugin/list`'s marketplaces, the Installed row, a plugin turned off or on
// (`plugins.<id>.enabled`) and what `plugin/read` says of a plugin before it is installed.
// Pure: no DOM.

import type { ConfigBatchWriteParams } from "./protocol/v2/ConfigBatchWriteParams";
import type { PluginDetail } from "./protocol/v2/PluginDetail";
import type { PluginMarketplaceEntry } from "./protocol/v2/PluginMarketplaceEntry";
import type { PluginSummary } from "./protocol/v2/PluginSummary";

/** Whether a plugin matches the search: every word in its name, id or keywords, case ignored. */
export function pluginMatches(plugin: Pick<PluginSummary, "id" | "name" | "keywords">, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const text = [plugin.name, plugin.id, ...(plugin.keywords ?? [])].join(" ").toLowerCase();
  return words.every((word) => text.includes(word));
}

/** The marketplaces with the plugins the search matches; a marketplace with none left out while searching. */
export function searchMarketplaces(marketplaces: PluginMarketplaceEntry[], query: string): PluginMarketplaceEntry[] {
  if (!query.trim()) return marketplaces;
  return marketplaces
    .map((market) => ({ ...market, plugins: market.plugins.filter((plugin) => pluginMatches(plugin, query)) }))
    .filter((market) => market.plugins.length > 0);
}

/** The installed plugins, once each (a plugin is listed by every marketplace that offers it), with the marketplace that lists it first. */
export function installedPlugins(marketplaces: PluginMarketplaceEntry[]): { plugin: PluginSummary; market: string }[] {
  const seen = new Set<string>();
  const installed: { plugin: PluginSummary; market: string }[] = [];
  for (const market of marketplaces) {
    for (const plugin of market.plugins) {
      if (!plugin.installed || seen.has(plugin.id)) continue;
      seen.add(plugin.id);
      installed.push({ plugin, market: market.name });
    }
  }
  return installed;
}

/**
 * The write that turns a plugin off or on: `plugins.<id>.enabled`, merged into the `plugins` table
 * (as a hook's state is), so an id with a dot in it needs no quoting in the key path.
 */
export function pluginEnabledEdit(id: string, enabled: boolean): ConfigBatchWriteParams {
  return { edits: [{ keyPath: "plugins", value: { [id]: { enabled } }, mergeStrategy: "upsert" }], filePath: null, expectedVersion: null, reloadUserConfig: true };
}

/** What the details show of a plugin before it is installed: what it would add to every thread. */
export interface PluginFacts {
  title: string;
  description: string | null;
  skills: string[];
  mcpServers: string[];
  hooks: string[];
  apps: string[];
}

/** `plugin/read`'s answer, in the lines the details show. */
export function pluginFacts(detail: PluginDetail): PluginFacts {
  const version = detail.summary.version ?? detail.summary.localVersion;
  return {
    title: version ? `${detail.summary.name} ${version}` : detail.summary.name,
    description: detail.description,
    skills: detail.skills.map((skill) => {
      const about = skill.shortDescription || skill.description;
      return about ? `$${skill.name}: ${about}` : `$${skill.name}`;
    }),
    mcpServers: detail.mcpServers,
    hooks: [...new Set(detail.hooks.map((hook) => hook.eventName))],
    apps: detail.apps.map((app) => app.name),
  };
}
