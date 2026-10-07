// The same cases as `ling-rs/node-locator/src/lib.rs`'s tests, so this port and the crate agree.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_MODEL_PORT, PROTO, SERVICE_TYPE, isNodeAt, modelUrl, parseNode, remoteNodeFrom, searchUrl, webUrl,
  type Node,
} from "./node_locator";

const node = (): Node => ({
  node: "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001",
  name: "gx10-1a2b",
  address: "192.168.50.105",
  model_port: 8000,
  web_port: 3000,
  search_port: 8888,
  version: "1.3.0",
  last_seen: "2026-10-02T09:10:04+01:00",
});

/** The file as `Node::render` writes it. */
const render = (n: Node) =>
  `{\n  "node": ${JSON.stringify(n.node)},\n  "name": ${JSON.stringify(n.name)},\n  "address": ${JSON.stringify(n.address)},\n  "model_port": ${n.model_port},\n` +
  (n.web_port ? `  "web_port": ${n.web_port},\n` : "") +
  (n.search_port ? `  "search_port": ${n.search_port},\n` : "") +
  `  "version": ${JSON.stringify(n.version)},\n  "last_seen": ${JSON.stringify(n.last_seen)}\n}\n`;

describe("the node locator, as the crate", () => {
  it("agrees with the node about the service type and the contract's version", () => {
    expect(SERVICE_TYPE).toBe("_mightling-node._tcp.local.");
    expect(PROTO).toBe(1);
  });

  it("round-trips the file", () => {
    expect(parseNode(render(node()))).toEqual(node());
  });

  it("treats what a node does not share as absent, not zero", () => {
    const unshared = { ...node(), web_port: null, search_port: null };
    const text = render(unshared);
    expect(text).not.toContain("web_port");
    expect(parseNode(text)).toEqual(unshared);
    expect(searchUrl(unshared)).toBeNull();
    expect(webUrl(unshared)).toBeNull();
    expect(parseNode('{"address": "10.0.0.2", "search_port": 0, "web_port": null}')?.search_port).toBeNull();
  });

  it("builds URLs from the address and brackets IPv6", () => {
    expect(modelUrl(node())).toBe("http://192.168.50.105:8000");
    expect(searchUrl(node())).toBe("http://192.168.50.105:8888");
    expect(webUrl(node())).toBe("http://192.168.50.105:3000");
    expect(modelUrl({ ...node(), address: "fe80::1%wlan0" })).toBe("http://[fe80::1%wlan0]:8000");
    expect(modelUrl({ ...node(), address: "spark.lan" })).toBe("http://spark.lan:8000");
  });

  it("needs only an address from a file set by hand", () => {
    const parsed = parseNode('{ "address" : "10.0.0.2" }');
    expect(parsed?.model_port).toBe(DEFAULT_MODEL_PORT);
    expect(parsed?.node).toBe("");
  });

  it("refuses anything that is not the file", () => {
    for (const text of ["", "[]", "{", '{"address": ""}', '{"name": "x"}', '{"address": {"a": 1}}', '{"address": "a"} x']) {
      expect(parseNode(text), text).toBeNull();
    }
  });

  it("keeps strings with quotes and escapes", () => {
    const odd = { ...node(), name: 'a "b" \\ c\n' };
    expect(parseNode(render(odd))).toEqual(odd);
  });

  it("never uses a remembered node on a node, and stays on loopback without one", () => {
    expect(remoteNodeFrom(true, node())).toBeNull();
    expect(remoteNodeFrom(false, null)).toBeNull();
    expect(remoteNodeFrom(false, node())).toEqual(node());
  });

  it("does not make a node of an empty id file", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-node-locator-"));
    const file = path.join(dir, "node-id");
    expect(isNodeAt(file)).toBe(false);
    fs.writeFileSync(file, "\n");
    expect(isNodeAt(file)).toBe(false);
    fs.writeFileSync(file, "7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001\n");
    expect(isNodeAt(file)).toBe(true);
    fs.rmSync(dir, { recursive: true, force: true });
  });
});
