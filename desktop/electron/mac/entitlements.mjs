// Which entitlements each piece of code in the signed Mac app gets (docs/dev/macos-signing.md).
// Only what is needed under the hardened runtime, instead of @electron/osx-sign's defaults (which
// add the camera, Bluetooth, USB, location, photos, and unsigned memory for the Plugin helper):
//
// - the app and its plain helper: V8's JIT and the microphone (dictation, egress.ts grantsRequest);
// - the Renderer, GPU and Plugin helpers, and any other helper app: V8's JIT;
// - the bundled codex-code-mode-host: V8's JIT (its JIT is on by default);
// - everything else (the bundled ling, the frameworks, dylibs, crashpad, ShipIt): none.
//
// No dependency, so tests/test_macos_signing.py can import it with plain Node.
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const dir = path.resolve(here, "..", "..", "..", "scripts", "macos", "entitlements");

export const APP = path.join(dir, "electron-app.plist");
export const HELPER = path.join(dir, "electron-helper.plist");
export const CODE_MODE_HOST = path.join(dir, "codex-code-mode-host.plist");

/**
 * The entitlements for one file osx-sign is about to sign.
 *
 * @param {string} file the path osx-sign passes: a `.app`, a main executable in one, or any other binary
 * @param {string} productName the app's name ("Mightling"), whose bundle and plain helper get APP
 * @returns {string | string[]} a plist path, or an empty list for no entitlements
 */
export function entitlementsFor(file, productName) {
  const normalized = file.split(path.sep).join("/");
  // A bundle, or the main executable that stands for it (codesign seals the bundle through it).
  const bundle = /([^/]+)\.app(?:\/Contents\/MacOS\/[^/]+)?$/.exec(normalized);
  if (bundle) {
    const name = bundle[1];
    return name === productName || name === `${productName} Helper` ? APP : HELPER;
  }
  if (normalized.endsWith("/Contents/Resources/codex-code-mode-host")) return CODE_MODE_HOST;
  return [];
}
