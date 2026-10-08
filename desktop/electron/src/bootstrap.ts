// After Electron is ready: one instance, the scheme handler, the session rules, the deep-link
// handler, then `main` with what the command line asked for.

import { app } from "electron";

import appConfig from "../app.json";
import { auditSeconds, workTarget } from "./args";
import { applySessionRules } from "./egress";
import { main } from "./main";

export async function bootstrap(): Promise<void> {
  // A second `ling-app` hands its arguments to the running one, which shows the window asked for.
  if (!app.requestSingleInstanceLock({ argv: process.argv.slice(1) })) {
    app.quit();
    return;
  }
  app.setAsDefaultProtocolClient(appConfig.scheme);
  applySessionRules();

  await main({ work: workTarget(process.argv.slice(1)), audit: auditSeconds() });
}
