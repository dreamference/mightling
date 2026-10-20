// Settings › Node (specs/DREAMFERENCE_MIGHTLING_NODE.md §6.4): the Mightling nodes on the local
// network and the one this machine uses, through the launcher's own commands, as a shell has them:
// `ling node list --json` (the same browse as `ling node list`, names only: no address, URL or
// configured host is in it) and `ling node use <name>`, which remembers the node in
// `$CODEX_HOME/node.json` as it always has (the last address stays its fallback after a failed
// browse). The window only ever sends a name, or a node's id when it advertises no name, from the
// list it was last given; never an address. Nothing browses until the page asks: an mDNS query at
// start-up would be a finding of the egress audit's app session. No Electron here.

import { execFile } from "node:child_process";

import { command } from "./platform";

export interface NodeEntry {
  name: string;
  id: string;
  /** What `ling node use` is given to choose this node; null when it can be chosen by no name. */
  choice: string | null;
  inUse: boolean;
  model: string | null;
  contextTokens: number | null;
  state: string;
  version: string;
  coding: boolean;
}

export interface NodeList {
  /** `client` chooses among the nodes; a `node` uses its own model server; `configured` names one. */
  thisMachine: "client" | "node" | "configured";
  nodes: NodeEntry[];
  /** The remembered node, when it did not answer this browse. */
  rememberedAway: { name: string; id: string } | null;
  /** A line about the network (Wi-Fi power saving) when a browse missed. */
  hint: string | null;
}

/** A browse waits up to a few seconds, and each answering node's model server two more. */
const TIMEOUT_MS = 30_000;

/** A name or id `ling node use` takes, and nothing that reads as an address or an option. */
const CHOICE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$/;

function run(ling: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const started = command(ling, args);
    execFile(started.file, started.args, { timeout: TIMEOUT_MS, shell: started.shell, windowsHide: true }, (error, stdout, stderr) => {
      if (error) reject(new Error(String(stderr || stdout || error.message).trim()));
      else resolve(String(stdout));
    });
  });
}

/** The list's JSON: the last line of `ling node list --json` that is an object (a notice may come first). */
export function parseList(stdout: string): NodeList {
  const line = stdout.split("\n").map((text) => text.trim()).filter((text) => text.startsWith("{")).pop();
  if (!line) throw new Error("ling node list --json printed no list: this ling is older than the app");
  const parsed = JSON.parse(line) as Partial<NodeList>;
  const machine = parsed.thisMachine === "node" || parsed.thisMachine === "configured" ? parsed.thisMachine : "client";
  const nodes = Array.isArray(parsed.nodes) ? parsed.nodes.filter((node): node is NodeEntry => typeof node === "object" && node !== null) : [];
  return {
    thisMachine: machine,
    nodes: nodes.map((node) => ({
      name: String(node.name ?? ""),
      id: String(node.id ?? ""),
      // Its name, else its id: whichever passes as a plain word (an mDNS name may hold a space or a dot).
      choice: [node.choice, node.name, node.id].find((text): text is string => typeof text === "string" && CHOICE.test(text)) ?? null,
      inUse: node.inUse === true,
      model: typeof node.model === "string" ? node.model : null,
      contextTokens: typeof node.contextTokens === "number" ? node.contextTokens : null,
      state: String(node.state ?? ""),
      version: String(node.version ?? ""),
      coding: node.coding === true,
    })),
    rememberedAway: parsed.rememberedAway && typeof parsed.rememberedAway === "object"
      ? { name: String(parsed.rememberedAway.name ?? ""), id: String(parsed.rememberedAway.id ?? "") }
      : null,
    hint: typeof parsed.hint === "string" ? parsed.hint : null,
  };
}

/** Lists the nodes and remembers which choices the window may send back. */
export class Nodes {
  private choices = new Set<string>();

  async list(ling: string): Promise<NodeList> {
    const list = parseList(await run(ling, ["node", "list", "--json"]));
    this.choices = new Set(list.thisMachine === "client" ? list.nodes.flatMap((node) => (node.choice ? [node.choice] : [])) : []);
    return list;
  }

  /** `ling node use <choice>` for a node of the last list; resolves with what it printed. */
  use(ling: string, choice: unknown): Promise<string> {
    if (typeof choice !== "string" || !CHOICE.test(choice) || !this.choices.has(choice)) {
      return Promise.reject(new Error("choose a node from the list"));
    }
    return run(ling, ["node", "use", choice]).then((output) => output.trim());
  }
}
