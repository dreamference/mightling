// The entry point (`main` in package.json), as the Codex app's `early-bootstrap.js`: everything
// that must happen before Electron is ready — the privileged scheme, the switches that keep
// Chromium quiet on the network, the name and data folder — then `bootstrap`.
//
// No GPU switches: Electron's defaults, as the Codex app runs
// (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md §3).

import path from "node:path";
import { app } from "electron";

import appConfig from "../app.json";
import { registerScheme } from "./app-protocol";
import { bootstrap } from "./bootstrap";
import { applySwitches } from "./egress";

applySwitches();
registerScheme();

// The data folder is the one the Tauri app used (and the rename migration moves), so Chat's cookies
// and the window state live where `ling app` and `ling-admin desktop` expect them.
app.setName(appConfig.productName);
const home = process.env.HOME || process.env.USERPROFILE;
if (home) app.setPath("userData", path.join(home, ".local", "share", appConfig.identifier));

app.whenReady().then(bootstrap).catch((error) => {
  console.error(`ling-app: ${error instanceof Error ? error.message : String(error)}`);
  app.exit(1);
});
