// No credential reaches a renderer, and the app handles none: Ask and Work both run in the app
// window on its own app-server, which needs no sign-in; the Onyx window's password sign-in and
// the separate Ask window's `ling web` link are gone. Checked in the sources that go into the
// preload and the app window's page, and in the
// built bundles when they exist (`npm run package` or `make` first; the release workflow runs
// this file again after `make`).
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = path.join(__dirname, "..");
const UI_SRC = path.join(ROOT, "..", "ui", "src");
const PRELOAD_BUNDLE = path.join(ROOT, ".vite", "build", "preload.js");
const MAIN_BUNDLE = path.join(ROOT, ".vite", "build", "early-bootstrap.js");
const RENDERER_BUNDLE = path.join(ROOT, ".vite", "renderer", "main_window");

// What a credential, or the code that handles one, would leave in a bundle: the file it lives in,
// the login endpoint, the session cookie, and the defaults the web UI had before the per-install
// password (none of which may come back).
const FORBIDDEN = [
  "chat-admin.json",
  "/api/auth/login",
  "fastapiusersauth",
  "admin@dreamference.dev",
  "__MIGHTLING_PASSWORD__",
  'password=dreamference',
  '"dreamference"',
];

function filesUnder(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return filesUnder(full);
    return /\.(js|mjs|cjs|ts|tsx|html|css|json|map)$/.test(entry.name) ? [full] : [];
  });
}

function offenders(files: string[]): string[] {
  return files.flatMap((file) => {
    const text = fs.readFileSync(file, "utf8");
    return FORBIDDEN.filter((needle) => text.includes(needle)).map((needle) => `${path.relative(ROOT, file)}: ${needle}`);
  });
}

describe("credentials stay in the main process", () => {
  it("are not in the preload's sources or Work's page", () => {
    const preloadSources = ["preload.ts", "api.ts"].map((name) => path.join(ROOT, "src", name));
    expect(offenders([...preloadSources, ...filesUnder(UI_SRC)])).toEqual([]);
    // The preload imports nothing but the message shapes.
    const preload = fs.readFileSync(path.join(ROOT, "src", "preload.ts"), "utf8");
    const imports = [...preload.matchAll(/from "([^"]+)"/g)].map((match) => match[1]);
    expect(imports.sort()).toEqual(["./api", "electron"]);
  });

  it("are not in the built bundles, the main process's included", { skip: !fs.existsSync(PRELOAD_BUNDLE) }, () => {
    const bundles = [PRELOAD_BUNDLE, MAIN_BUNDLE, ...filesUnder(RENDERER_BUNDLE)];
    expect(bundles.length).toBeGreaterThan(2);
    expect(offenders(bundles)).toEqual([]);
    // The check reads the right bundle: the main process does carry the app-server's command line.
    const main = fs.readFileSync(MAIN_BUNDLE, "utf8");
    expect(main).toContain("features.code_mode_host=true");
    // And nothing of the separate Ask window it no longer opens: no `ling web` sign-in link.
    expect(main).not.toContain("--print-url");
  });
});
