// `Mightling --version`: the app's version and the bundled `ling`'s, then exit 0, before anything
// else the app does (no window, no app-server, no single-instance lock, no D-Bus), so it answers
// with no display and any HOME. `ling` is not run: its version is the line forge.config.js's
// `prePackage` hook wrote into `resources/` at build time. No `electron` import, so it is tested
// as is.

import fs from "node:fs";
import path from "node:path";

/** The file beside the bundled `ling` holding its `--version` line (forge.config.js writes it). */
export const LING_VERSION_FILE = "ling.version";

/** Whether the command line asks for the version. */
export function wantsVersion(args: readonly string[]): boolean {
  return args.includes("--version");
}

/**
 * What `--version` prints: `<product> <version>`, then the bundled `ling`'s version line.
 *
 * @param resourcesPath - the packaged app's resources folder, or `null` when not packaged
 */
export function versionText(
  productName: string,
  appVersion: string,
  resourcesPath: string | null,
  read: (file: string) => string = (file) => fs.readFileSync(file, "utf8"),
): string {
  let ling = "ling: not bundled";
  if (resourcesPath !== null) {
    try {
      ling = read(path.join(resourcesPath, LING_VERSION_FILE)).split("\n")[0].trim() || "ling: version unknown";
    } catch {
      // No file: no `ling` was staged when the app was packaged.
    }
  }
  return `${productName} ${appVersion}\n${ling}\n`;
}
