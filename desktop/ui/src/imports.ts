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
 * Items the window does not import: plugins come from their marketplaces, which an import would
 * clone over the network (and the vendor's catalogue is closed, patch 0015); install them from
 * Skills and tools › Plugins instead.
 */
export function refusal(item: Pick<ExternalAgentConfigMigrationItem, "itemType">): string | null {
  return item.itemType === "PLUGINS" ? "Plugins come from their marketplaces over the network: install them from the Plugins tab." : null;
}

/** Ticked to begin with: everything importable but past sessions, which can be many. */
export function startsTicked(item: Pick<ExternalAgentConfigMigrationItem, "itemType">): boolean {
  return !refusal(item) && item.itemType !== "SESSIONS";
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
