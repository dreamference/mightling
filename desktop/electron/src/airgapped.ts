// The air-gap level in force for a thread, or the configured one: Work's title bar shows it and the
// permission picker disables Full Access at `on` (a courtesy; the refusal that binds is the
// server's, specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §8.2). Asked of `ling airgapped --thread <id>`,
// the launcher's own resolver, rather than kept as a fourth copy of the resolver crate.

import { execFile } from "node:child_process";

import type { Airgapped } from "./api";

/** The first line `ling airgapped` prints: `Airgapped: <level> (<source>)`. */
export function parseStatus(text: string): Airgapped | null {
  const match = text.match(/^Airgapped:\s+(off|on)\s+\((.*)\)\s*$/m);
  return match ? { level: match[1] as Airgapped["level"], source: match[2] } : null;
}

export function airgapped(ling: string, thread: string | null): Promise<Airgapped> {
  const args = ["airgapped", ...(thread && /^[A-Za-z0-9-]+$/.test(thread) ? ["--thread", thread] : [])];
  return new Promise((resolve) => {
    execFile(ling, args, { timeout: 10_000 }, (_error, stdout) => {
      resolve(parseStatus(String(stdout)) ?? { level: "off", source: "unknown (ling airgapped did not answer)" });
    });
  });
}
