// Recommended MCP servers (DESKTOP §16 B26, DESKTOP_PARITY §9.5.9): a short list of well-known
// open-source servers that run on this machine, each filling in Skills and tools' Add server form.
// Nothing installs by itself: the user reads the command and presses Add. Every one here is started
// by a package runner (`npx`, `uvx`) that fetches it on first use, so each is marked as needing the
// network and none is offered while `/airgapped` is on.

/** One recommended server: the form's fields, what it does, and whether it needs the network. */
export interface RecommendedServer {
  name: string;
  title: string;
  description: string;
  command: string;
  args: string;
  /** A package runner fetches it (and `fetch` reaches the web itself): not offered at `/airgapped on`. */
  network: string;
}

/** The reference servers of the Model Context Protocol project (MIT), and Microsoft's Playwright server (Apache-2.0). */
export const RECOMMENDED_SERVERS: readonly RecommendedServer[] = [
  { name: "filesystem", title: "Filesystem", description: "Reads and writes files in the folders named after it, and no others.", command: "npx", args: "-y @modelcontextprotocol/server-filesystem ~/Documents", network: "npx fetches it from the npm registry" },
  { name: "git", title: "Git", description: "Reads a repository's log, diffs and branches, and commits.", command: "uvx", args: "mcp-server-git --repository .", network: "uvx fetches it from PyPI" },
  { name: "memory", title: "Memory", description: "A knowledge graph the agent keeps across threads, in a local file.", command: "npx", args: "-y @modelcontextprotocol/server-memory", network: "npx fetches it from the npm registry" },
  { name: "time", title: "Time", description: "The current time and conversions between time zones.", command: "uvx", args: "mcp-server-time", network: "uvx fetches it from PyPI" },
  { name: "sequential-thinking", title: "Sequential thinking", description: "A scratch pad for working a problem through in numbered steps.", command: "npx", args: "-y @modelcontextprotocol/server-sequential-thinking", network: "npx fetches it from the npm registry" },
  { name: "fetch", title: "Fetch", description: "Fetches a web page as Markdown.", command: "uvx", args: "mcp-server-fetch", network: "uvx fetches it from PyPI, and it reaches the web itself" },
  { name: "playwright", title: "Playwright", description: "Drives a browser: opens pages, clicks, fills forms, reads what is shown.", command: "npx", args: "-y @playwright/mcp", network: "npx fetches it from the npm registry" },
];

/** The servers to offer: none while airgapped is on (each needs the network), and none already configured under its name. */
export function offeredServers(airgapped: boolean, configured: string[]): RecommendedServer[] {
  if (airgapped) return [];
  return RECOMMENDED_SERVERS.filter((server) => !configured.includes(server.name));
}
