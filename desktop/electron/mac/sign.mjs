// Signs the packaged Mac app with the Developer ID and the hardened runtime
// (docs/dev/macos-signing.md), after Forge's `package` and its postPackage hook, whose fuse flip
// leaves an ad-hoc signature:
//
//   node mac/sign.mjs <path to Mightling.app>
//
// MAC_SIGN_IDENTITY (the identity's SHA-1) and MAC_SIGN_KEYCHAIN come from
// scripts/macos/import_identity.sh. @electron/osx-sign signs every piece of code from the inside
// out (helpers, frameworks, the bundled ling and codex-code-mode-host), each with the entitlements
// of entitlements.mjs and a secure timestamp, then checks the bundle with
// `codesign --verify --deep --strict`.
import path from "node:path";

import { sign } from "@electron/osx-sign";

import { entitlementsFor } from "./entitlements.mjs";

const app = process.argv[2];
const identity = process.env.MAC_SIGN_IDENTITY;
const keychain = process.env.MAC_SIGN_KEYCHAIN;
if (!app || !app.endsWith(".app") || !identity || !keychain) {
  console.error("usage: MAC_SIGN_IDENTITY=<sha-1> MAC_SIGN_KEYCHAIN=<keychain> node mac/sign.mjs <app>.app");
  process.exit(2);
}
const productName = path.basename(app, ".app");

await sign({
  app,
  platform: "darwin",
  type: "distribution",
  // The hash, as import_identity.sh found it in the job's keychain: no lookup by name, so the
  // certificate's name (the enrolled account's) is never printed.
  identity,
  identityValidation: false,
  keychain,
  // No provisioning profile, no sandbox: nothing to add to the entitlements.
  preAutoEntitlements: false,
  optionsForFile: (file) => ({ hardenedRuntime: true, entitlements: entitlementsFor(file, productName) }),
});
console.log(`Signed ${app} with the Developer ID and the hardened runtime`);
