// Settings › Node's calls (nodes.ts) through a stand-in `ling node`: the list read from its JSON,
// and a choice sent back only when the last list offered it, never an address.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { Nodes, parseList } from "./nodes";
import { nodeScript } from "./test-ling";

const LIST = {
  thisMachine: "client",
  nodes: [
    { name: "spark-1", id: "11111111-aaaa", choice: "spark-1", inUse: true, model: "RadixArk/Qwen3.8-27B-NVFP4", contextTokens: 262144, state: "ready", version: "1.7.1", coding: true },
    { name: "", id: "22222222-bbbb", choice: "22222222-bbbb", inUse: false, model: null, contextTokens: null, state: "stopped", version: "1.7.1", coding: true },
    { name: "lab box.local", id: "", choice: "lab box.local", inUse: false, model: null, contextTokens: null, state: "", version: "", coding: false },
  ],
  rememberedAway: null,
  hint: null,
};

describe("Settings › Node", () => {
  it("reads the list from the last JSON line, and keeps only plain names and ids as choices", () => {
    const list = parseList(`Note: node spark-1 runs Mightling 1.7.0, this machine 1.7.1.\n${JSON.stringify(LIST)}\n`);
    expect(list.thisMachine).toBe("client");
    expect(list.nodes.map((node) => node.choice)).toEqual(["spark-1", "22222222-bbbb", null]);
    expect(list.nodes[0].inUse).toBe(true);
    expect(() => parseList("No Mightling node answered on this network.\n")).toThrow("older than the app");
  });

  it("runs ling node list --json and ling node use <name>, and refuses what the list did not offer", async () => {
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-nodes-")));
    const log = path.join(home, "calls.txt");
    const ling = nodeScript(home, "ling", `require("fs").appendFileSync(${JSON.stringify(log)}, process.argv.slice(2).join(" ") + "\\n");
if (process.argv[3] === "list") console.log(${JSON.stringify(JSON.stringify(LIST))});
else console.log("Using node " + process.argv[4] + " at http://192.168.0.106:8000.");
`);
    const nodes = new Nodes();
    // Nothing listed yet: nothing may be chosen.
    await expect(nodes.use(ling, "spark-1")).rejects.toThrow("from the list");
    const list = await nodes.list(ling);
    expect(list.nodes).toHaveLength(3);
    await expect(nodes.use(ling, "22222222-bbbb")).resolves.toContain("Using node");
    for (const refused of ["192.168.0.106", "spark-9", "--help", "spark-1 ; rm", "lab box.local", 7]) {
      await expect(nodes.use(ling, refused)).rejects.toThrow("from the list");
    }
    expect(fs.readFileSync(log, "utf8").trim().split("\n")).toEqual(["node list --json", "node use 22222222-bbbb"]);
  });

  it("offers no choice on a node or with a configured host", async () => {
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-nodes-")));
    const ling = nodeScript(home, "ling", `console.log(${JSON.stringify(JSON.stringify({ ...LIST, thisMachine: "node" }))});\n`);
    const nodes = new Nodes();
    expect((await nodes.list(ling)).thisMachine).toBe("node");
    await expect(nodes.use(ling, "spark-1")).rejects.toThrow("from the list");
  });
});
