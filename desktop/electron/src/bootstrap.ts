// After Electron is ready: one instance, the scheme handler, the session rules, the deep-link
// handler, then `main` with what the command line asked for.

import { app } from "electron";

import appConfig from "../app.json";
import { claimAppIdentity } from "./app-identity";
import { auditSeconds, workTarget } from "./args";
import { applySessionRules } from "./egress";
import { main } from "./main";

export async function bootstrap(): Promise<void> {
  // A second `ling-app` hands its arguments to the running one, which shows the window asked for.
  if (!app.requestSingleInstanceLock({ argv: process.argv.slice(1) })) {
    app.quit();
    return;
  }
  for (const scheme of [appConfig.scheme, ...appConfig.legacySchemes]) app.setAsDefaultProtocolClient(scheme);
  // Windows: the app's ID, before any notification or window (app-identity.ts).
  claimAppIdentity();
  applySessionRules();

  await main({ work: workTarget(process.argv.slice(1)), audit: auditSeconds() });
}
