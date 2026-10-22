// Importing another agent's configuration and histories (DESKTOP_PARITY §2.11, Phase 3 item 8),
// through the server's `externalAgentConfig/detect|import|import/readHistories`: what was found,
// in words, which items are ticked to begin with, and what an import did.

import type { ExternalAgentConfigImportTypeResult } from "./protocol/v2/ExternalAgentConfigImportTypeResult";
import type { ExternalAgentConfigMigrationItem } from "./protocol/v2/ExternalAgentConfigMigrationItem";
import type { ExternalAgentConfigMigrationItemType } from "./protocol/v2/ExternalAgentConfigMigrationItemType";

export const TYPE_LABELS: Record<ExternalAgentConfigMigrationItemType, string> = {
  AGENTS_MD: "Instructions (AGENTS.md)",
  CONFIG: "Settings",
  SKILLS: "Skills",
  PLUGINS: "Plugins",
  MCP_SERVER_CONFIG: "MCP servers",
  SUBAGENTS: "Subagents",
  HOOKS: "Hooks",
  COMMANDS: "Commands",
  MEMORY: "Memory",
  SESSIONS: "Past sessions",
};

/**
 * What to know before importing an item: plugins are fetched from their marketplaces over the
 * network (git, npm), and while airgapped is on the app leaves them out of an import
 * (ling-rs/web/policy.json, `airgappedRefusedImportItems`; the vendor's catalogue stays closed,
 * patch 0015).
 */
export function caveat(item: Pick<ExternalAgentConfigMigrationItem, "itemType">): string | null {
  return item.itemType === "PLUGINS" ? "Fetched from their marketplaces over the network; left out while airgapped is on." : null;
}

/** Ticked to begin with: everything cheap and local, so not past sessions (they can be many) or plugins (fetched). */
export function startsTicked(item: Pick<ExternalAgentConfigMigrationItem, "itemType">): boolean {
  return item.itemType !== "SESSIONS" && item.itemType !== "PLUGINS";
}

/** Where an item applies: every project (the home folder's), or one project. */
export function scopeOf(item: Pick<ExternalAgentConfigMigrationItem, "cwd">): string {
  return item.cwd ? `for ${item.cwd.split(/[\\/]/).filter(Boolean).pop() ?? item.cwd}` : "for every project";
}

/** An import's results in one line per kind: "Skills: 3 imported, 1 failed (…)". */
export function resultLines(results: ExternalAgentConfigImportTypeResult[]): string[] {
  return results.map((result) => {
    const label = TYPE_LABELS[result.itemType] ?? result.itemType;
    const failed = result.failures.length ? `, ${result.failures.length} failed (${result.failures[0].message})` : "";
    return `${label}: ${result.successes.length} imported${failed}`;
  });
}

/** A stable key for an item in the list. */
export function itemKey(item: ExternalAgentConfigMigrationItem): string {
  return `${item.itemType}|${item.cwd ?? ""}|${item.description}`;
}
