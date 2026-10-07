// Where the Mightling node is (specs/DREAMFERENCE_MIGHTLING_NODE.md §6.1, §6.2): a port of the Rust
// crate `ling-rs/node-locator` for the app's main process. The app only *reads* `node.json` and
// `node-id`; the launcher is the one that remembers a node. `node_locator.test.ts` runs the same
// cases as the crate's tests, which is what keeps the two in step now that they are not one file.

import fs from "node:fs";
import path from "node:path";

/** The DNS-SD service type a node is advertised under. */
export const SERVICE_TYPE = "_mightling-node._tcp.local.";

/** The version of the advertised contract this code understands. */
export const PROTO = 1;

/** The file under `$CODEX_HOME` that remembers the node. */
export const NODE_FILE = "node.json";

/** The model server's port when an address is given without one. */
export const DEFAULT_MODEL_PORT = 8000;

/** A node as a client remembers it. */
export interface Node {
  /** The node's stable id. Empty for a node set by address that never answered a browse. */
  node: string;
  name: string;
  /** An IP address, or a host name the user gave to `ling node use`. */
  address: string;
  model_port: number;
  web_port: number | null;
  search_port: number | null;
  version: string;
  last_seen: string;
}

/** An address as it goes into a URL: IPv6 in brackets. */
export function urlHost(address: string): string {
  return address.includes(":") && !address.startsWith("[") ? `[${address}]` : address;
}

export const modelUrl = (node: Node) => `http://${urlHost(node.address)}:${node.model_port}`;
export const webUrl = (node: Node) => (node.web_port ? `http://${urlHost(node.address)}:${node.web_port}` : null);
export const searchUrl = (node: Node) =>
  node.search_port ? `http://${urlHost(node.address)}:${node.search_port}` : null;

/**
 * Reads `node.json`'s text: one flat JSON object whose values are strings, numbers, booleans or
 * null, with an address. Anything nested, or anything else, is refused, as the crate refuses it.
 */
export function parseNode(text: string): Node | null {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const fields = value as Record<string, unknown>;
  for (const member of Object.values(fields)) {
    if (member !== null && typeof member === "object") return null;
  }
  const str = (key: string): string => (typeof fields[key] === "string" ? (fields[key] as string) : "");
  const port = (key: string): number | null => {
    const raw = fields[key];
    const number = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : NaN;
    return Number.isInteger(number) && number > 0 && number <= 65535 ? number : null;
  };
  const address = str("address");
  if (!address) return null;
  return {
    node: str("node"),
    name: str("name"),
    address,
    model_port: port("model_port") ?? DEFAULT_MODEL_PORT,
    web_port: port("web_port"),
    search_port: port("search_port"),
    version: str("version"),
    last_seen: str("last_seen"),
  };
}

/** The user's home folder: `HOME`, or `USERPROFILE` on Windows. */
export function homeDir(env: NodeJS.ProcessEnv = process.env): string | null {
  return env.HOME || env.USERPROFILE || null;
}

/** `$CODEX_HOME`, or `~/.mightling`. */
export function codexHome(env: NodeJS.ProcessEnv = process.env): string | null {
  if (env.CODEX_HOME) return env.CODEX_HOME;
  const home = homeDir(env);
  return home ? path.join(home, ".mightling") : null;
}

export function nodeFile(env: NodeJS.ProcessEnv = process.env): string | null {
  const home = codexHome(env);
  return home ? path.join(home, NODE_FILE) : null;
}

/** The file that marks a machine as a node, and holds its id. */
export function nodeIdFile(env: NodeJS.ProcessEnv = process.env): string | null {
  const home = homeDir(env);
  return home ? path.join(home, ".config", "dreamference", "node-id") : null;
}

/** `isNode` for a given id file: it must hold something, not merely exist. */
export function isNodeAt(file: string): boolean {
  try {
    return fs.readFileSync(file, "utf8").trim().length > 0;
  } catch {
    return false;
  }
}

/** Whether this machine is a node: it then uses its own services on loopback and never browses. */
export function isNode(env: NodeJS.ProcessEnv = process.env): boolean {
  const file = nodeIdFile(env);
  return file !== null && isNodeAt(file);
}

export function rememberedAt(file: string): Node | null {
  try {
    return parseNode(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

/** The remembered node, if this client has one. */
export function remembered(env: NodeJS.ProcessEnv = process.env): Node | null {
  const file = nodeFile(env);
  return file ? rememberedAt(file) : null;
}

/** `remoteNode` with its two inputs given: `null` on a node, or with nothing remembered. */
export function remoteNodeFrom(node: boolean, rememberedNode: Node | null): Node | null {
  return node ? null : rememberedNode;
}
