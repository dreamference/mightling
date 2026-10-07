// The decision rules of `discover.rs`, unchanged.
import { describe, expect, it } from "vitest";

import { NONE_FOUND, bestAddress, decide } from "./discover";
import { namedNode } from "./discover";
import { toNode } from "./forwarder";
import type { Node } from "./node_locator";

const node = (name: string, id: string, address: string): Node => ({
  node: id, name, address, model_port: 8000, web_port: 3000, search_port: null, version: "", last_seen: "",
});

describe("choosing the node to forward to", () => {
  it("follows the remembered node to its new address and never swaps it for another", () => {
    const remembered = node("spark-1", "1111", "192.168.50.105");
    expect(decide(remembered, [node("spark-1", "1111", "192.168.50.200")])).toEqual(toNode("192.168.50.200:3000"));
    expect(decide(remembered, [])).toEqual(toNode("192.168.50.105:3000"));
    expect(decide(remembered, [node("spark-2", "2222", "192.168.50.106")])).toEqual(toNode("192.168.50.105:3000"));
  });

  it("uses one node, asks about several, and says when there is none", () => {
    const one = node("spark-1", "1111", "192.168.50.105");
    const two = node("spark-2", "2222", "fd00::6");
    expect(decide(null, [one])).toEqual(toNode("192.168.50.105:3000"));
    expect(decide(null, [two])).toEqual(toNode("[fd00::6]:3000"));
    const several = decide(null, [one, two]);
    expect(several.kind).toBe("message");
    if (several.kind === "message") {
      expect(several.text).toContain("spark-1 (192.168.50.105), spark-2 (fd00::6)");
      expect(several.text).toContain("ling node use <name>");
    }
    expect(decide(null, [])).toEqual({ kind: "message", text: NONE_FOUND });
  });

  it("says, not forwards, when a node keeps its web UI to itself", () => {
    const unshared = { ...node("spark-1", "1111", "192.168.50.105"), web_port: null };
    const decision = decide(null, [unshared]);
    expect(decision.kind).toBe("message");
    if (decision.kind === "message") expect(decision.text).toContain("spark-1 does not share its web UI");
  });

  it("prefers IPv4, then a routable IPv6, never loopback or link-local", () => {
    expect(bestAddress(["fe80::1", "fd00::6", "192.168.50.5"])).toBe("192.168.50.5");
    expect(bestAddress(["fe80::1", "fd00::6"])).toBe("fd00::6");
    expect(bestAddress(["127.0.0.1", "::1", "fe80::1"])).toBeNull();
    expect(bestAddress(["169.254.3.3", "10.0.0.2"])).toBe("10.0.0.2");
  });
});

describe("a model server named outright", () => {
  it("is the node, and loopback means this machine", () => {
    expect(namedNode({})).toBeUndefined();
    expect(namedNode({ DREAMFERENCE_VLLM_HOST: "http://localhost:8000" })).toBeNull();
    expect(namedNode({ DREAMFERENCE_VLLM_HOST: "http://127.0.0.1:8000/v1" })).toBeNull();
    expect(namedNode({ MIGHTLING_NODE: "192.168.1.20" })).toEqual({ kind: "node", target: "192.168.1.20:3000" });
    expect(namedNode({ DREAMFERENCE_VLLM_HOST: "http://[fe80::1]:8000" })).toEqual({ kind: "node", target: "[fe80::1]:3000" });
  });
});
