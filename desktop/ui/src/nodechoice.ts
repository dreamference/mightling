// The start-up screen's node picker: when `ling app-server` exits because several Mightling nodes
// answered and none is chosen, the window lists them and the user picks one (NODE §6.3). The
// launcher's refusal is the signal; the list and the choice go through `node/list` and `node/use`.

import type { NodeEntry } from "./bridge";

/** Whether the agent's last messages say it stopped for want of a chosen node. */
export function needsNodeChoice(lines: string[]): boolean {
  return lines.some((line) => line.includes("Several Mightling nodes are on this network"));
}

/** The nodes the user can pick from: those the list offers a choice for. */
export function pickable(nodes: NodeEntry[]): NodeEntry[] {
  return nodes.filter((node) => node.choice);
}
