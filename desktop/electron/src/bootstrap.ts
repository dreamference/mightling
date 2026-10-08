// After Electron is ready: one instance, the scheme handler, the session rules, the forwarder on a
// client, the deep-link handler, then `main` with what the command line asked for.

import { app } from "electron";

import appConfig from "../app.json";
import { auditSeconds, workTarget } from "./args";
import { chatUrl } from "./chat";
import { upstream } from "./discover";
import { applySessionRules } from "./egress";
import { start as startForwarder } from "./forwarder";
import { main } from "./main";

export async function bootstrap(): Promise<void> {
  // A second `ling-app` hands its arguments to the running one, which shows the window asked for.
  if (!app.requestSingleInstanceLock({ argv: process.argv.slice(1) })) {
    app.quit();
    return;
  }
  app.setAsDefaultProtocolClient(appConfig.scheme);

  // Not a node: bring the node's web UI to loopback before any window asks for it.
  let port: number | null = null;
  const target = await upstream();
  if (target !== null) {
    try {
      port = await startForwarder(target);
    } catch (error) {
      console.error(`ling-app: could not bind a loopback port for the node's web UI: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  applySessionRules(new URL(chatUrl(port)).origin);

  await main({ port, work: workTarget(process.argv.slice(1)), audit: auditSeconds() });
}
