// Where the Chat window's web UI is (specs/DREAMFERENCE_MIGHTLING_NODE.md §6.1, §7).
//
// On a node the window loads the local web UI, as it always has. On any other machine the web UI
// is the node's, and `forwarder.ts` brings it to `localhost`. This decides which, with the same
// rules as the `ling` launcher: the node remembered in `node.json` is looked for by its id, a
// single node found is used, and with several and none remembered the app does not guess: it says
// how to choose (`ling node use <name>`). The app reads `node.json` and never writes it.

import net from "node:net";

import { PREFERRED_PORT, toMessage, toNode, type Upstream } from "./forwarder";
import * as locator from "./node_locator";
import type { Node } from "./node_locator";

const BROWSE_TIMEOUT_MS = 2000;
const SETTLE_MS = 400;

export const NONE_FOUND =
  "No Mightling node found on this network.\n" +
  "Start one on a GB10: ling-admin server start, then ling-admin node enable.\n" +
  "Or name one by its address, in a terminal: ling node use <address>";

/** What the forwarder should do, or `null` on a node, where there is nothing to forward. */
export async function upstream(): Promise<Upstream | null> {
  const named = namedNode();
  if (named !== undefined) return named;
  if (locator.isNode()) return null;
  const rememberedNode = locator.remembered();
  const wanted = rememberedNode?.node || null;
  // A node set by address and never seen in a browse has no id to look for.
  const found = rememberedNode && !wanted ? [] : await browse(wanted);
  const decision = decide(rememberedNode, found);
  // No node anywhere, but a web UI answers on this machine: a node that has not written its id
  // yet (installed before the split). Keep working as before, as the launcher does for the model.
  if (decision.kind === "message" && decision.text === NONE_FOUND && (await localWebUiAnswers())) return null;
  return decision;
}

/**
 * The launcher's first tier: a model server named outright (`DREAMFERENCE_VLLM_HOST`, or
 * `MIGHTLING_NODE`) is the node, and its web UI is on the same machine. A loopback host means this
 * machine, so nothing is forwarded; nothing named means `undefined`, and the tiers go on. This is
 * what lets an unattended run (the egress audit) name the server instead of browsing, because a
 * browse is a multicast DNS query.
 */
export function namedNode(env: NodeJS.ProcessEnv = process.env): Upstream | null | undefined {
  const named = env.DREAMFERENCE_VLLM_HOST || env.MIGHTLING_NODE;
  if (!named) return undefined;
  let host: string;
  try {
    host = new URL(named.includes("://") ? named : `http://${named}`).hostname;
  } catch {
    return undefined;
  }
  const bare = host.replace(/^\[|\]$/g, "");
  if (bare === "localhost" || bare === "::1" || /^127\./.test(bare)) return null;
  return toNode(`${locator.urlHost(bare)}:${PREFERRED_PORT}`);
}

function localWebUiAnswers(): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port: PREFERRED_PORT, timeout: 500 });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
  });
}

/** The decision, with the browse's answers given. */
export function decide(rememberedNode: Node | null, found: Node[]): Upstream {
  if (rememberedNode) {
    const seen = found.find((node) => rememberedNode.node !== "" && node.node === rememberedNode.node);
    // Nothing answered: its last address. Others answered but not this one: still the remembered
    // node; a different one is never adopted silently.
    return webUi(seen ?? rememberedNode);
  }
  if (found.length === 0) return toMessage(NONE_FOUND);
  if (found.length === 1) return webUi(found[0]);
  const names = found.map((node) => `${label(node)} (${node.address})`).join(", ");
  return toMessage(`Several Mightling nodes are on this network: ${names}.\nChoose one in a terminal: ling node use <name>`);
}

function webUi(node: Node): Upstream {
  if (node.web_port) return toNode(`${locator.urlHost(node.address)}:${node.web_port}`);
  return toMessage(`The Mightling node ${label(node)} does not share its web UI.\nOn the node: ling-admin node enable (without --no-web)`);
}

const label = (node: Node) => node.name || node.address;

interface Answer {
  name: string;
  port: number;
  addresses: string[];
  txt: Record<string, string>;
}

/** Browses `_mightling-node._tcp`, as the launcher does (`ling-rs/src/node.rs`), through multicast-dns. */
export async function browse(wanted: string | null): Promise<Node[]> {
  let mdns: import("multicast-dns").MulticastDNS;
  try {
    const multicastDns = (await import("multicast-dns")).default;
    mdns = multicastDns();
  } catch {
    return [];
  }
  const type = locator.SERVICE_TYPE.replace(/\.$/, "");
  const found: Node[] = [];
  const partial = new Map<string, Answer>();
  return new Promise((resolve) => {
    const deadline = Date.now() + BROWSE_TIMEOUT_MS;
    let settle: number | null = null;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      try {
        mdns.destroy();
      } catch {
        // Already gone.
      }
      resolve(found);
    };
    const tick = () => {
      const limit = settle === null ? deadline : Math.min(settle, deadline);
      if (Date.now() >= limit) finish();
      else setTimeout(tick, 50);
    };
    mdns.on("response", (response) => {
      const records = [...response.answers, ...response.additionals];
      for (const record of records) {
        if (record.type === "PTR" && record.name === type) partial.set(record.data, partial.get(record.data) ?? { name: record.data, port: 0, addresses: [], txt: {} });
      }
      for (const record of records) {
        const entry = partial.get(record.name);
        if (record.type === "SRV" && entry) {
          entry.port = record.data.port;
          for (const address of records) {
            if ((address.type === "A" || address.type === "AAAA") && address.name === record.data.target) entry.addresses.push(address.data);
          }
        }
        if (record.type === "TXT" && entry) {
          for (const item of record.data) {
            const [key, ...rest] = item.toString().split("=");
            entry.txt[key] = rest.join("=");
          }
        }
      }
      for (const entry of partial.values()) {
        const address = bestAddress(entry.addresses);
        if (!entry.port || !address) continue;
        const port = (key: string) => {
          const number = Number(entry.txt[key]);
          return Number.isInteger(number) && number > 0 ? number : null;
        };
        const node: Node = {
          node: entry.txt.node ?? "",
          name: entry.name.endsWith(`.${type}`) ? entry.name.slice(0, -type.length - 1) : entry.name,
          address,
          model_port: entry.port,
          web_port: port("web"),
          search_port: port("search"),
          version: entry.txt.version ?? "",
          last_seen: "",
        };
        if (!found.some((other) => other.node === node.node && other.name === node.name)) found.push(node);
        if (wanted !== null && node.node === wanted) return finish();
        settle ??= Date.now() + SETTLE_MS;
      }
    });
    mdns.query({ questions: [{ name: type, type: "PTR" }] });
    tick();
  });
}

/** IPv4 first, then an IPv6 address that needs no scope; never loopback or link-local IPv6. */
export function bestAddress(addresses: string[]): string | null {
  const rank = (address: string): number | null => {
    if (net.isIPv4(address)) {
      if (address.startsWith("127.")) return null;
      return address.startsWith("169.254.") ? 1 : 0;
    }
    if (net.isIPv6(address)) {
      const lower = address.toLowerCase();
      if (lower === "::1" || /^fe[89ab]/.test(lower)) return null;
      return 3;
    }
    return null;
  };
  const usable = addresses
    .map((address) => [rank(address), address] as const)
    .filter((pair): pair is readonly [number, string] => pair[0] !== null)
    .sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1]));
  return usable[0]?.[1] ?? null;
}
