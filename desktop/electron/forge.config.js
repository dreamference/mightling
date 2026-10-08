// Electron Forge, the way the Codex desktop app is packaged (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md
// §3): Vite builds the main process, the preload and Work's UI into `.vite/`; `package` writes
// `out/Mightling-linux-<arch>/`; `make` wraps that in a `.deb` (and a zip). The fuses are flipped on
// the packaged binary. `ling` and `codex-code-mode-host` ride along as extra resources, copied into
// `resources/` by `ling-admin desktop build` before `make` runs.
const fs = require("node:fs");
const path = require("node:path");

// @electron/fuses 2 (Forge 7's own fuses plugin pins the 1.x line, which has no WasmTrapHandlers
// fuse), flipped on the packaged binary in the postPackage hook below.
const { FuseV1Options, FuseVersion, flipFuses } = require("@electron/fuses");

const app = require("./app.json");
const pkg = require("./package.json");

const resources = path.join(__dirname, "resources");
const extraResource = ["ling", "codex-code-mode-host", "rg"]
  .map((name) => path.join(resources, name))
  .filter((file) => fs.existsSync(file));

module.exports = {
  packagerConfig: {
    name: app.productName,
    executableName: app.executable,
    appBundleId: app.identifier,
    appCopyright: "Copyright (C) 2026 Dreamference contributors. AGPL-3.0-or-later.",
    asar: true,
    icon: path.join(__dirname, "icons", "icon"),
    extraResource,
    // The asar holds what the app loads: the Vite build (main, preload, Work's page, with
    // multicast-dns bundled in), the icons and package.json. Sources, configs, tests, the staged
    // binaries (extra resources above) and node_modules stay out.
    ignore: (file) =>
      !(file === "" || file === "/package.json" || file === "/.vite" || file.startsWith("/.vite/") || file === "/icons" || file.startsWith("/icons/")),
  },
  rebuildConfig: {},
  makers: [
    {
      name: "@electron-forge/maker-deb",
      config: {
        options: {
          name: app.packageName,
          productName: app.productName,
          genericName: "AI assistant",
          description: "Private AI on your GB10: a coding agent and a chat assistant, nothing leaves your machine.",
          productDescription:
            "Mightling's desktop app. Chat is the local web UI in a window of its own; Work drives the coding agent, ling, which is bundled inside the app.",
          bin: app.executable,
          icon: path.join(__dirname, "icons", "icon.png"),
          categories: ["Utility", "Development"],
          mimeType: [`x-scheme-handler/${app.scheme}`],
          section: "devel",
          priority: "optional",
          homepage: "https://github.com/dreamference/mightling",
          maintainer: "Dreamference <dgxcoder@dreamference.ai>",
          scripts: {
            postinst: path.join(__dirname, "linux", "postinst"),
            prerm: path.join(__dirname, "linux", "prerm"),
          },
          // Chromium's own dependencies (electron-installer-debian's defaults) plus the sound server
          // the microphone and notification sounds use. None of the Codex app's extras (its TPM and
          // USB libraries).
          depends: [
            "libgtk-3-0 | libgtk-3-0t64",
            "libnotify4",
            "libnss3",
            "libxss1",
            "libxtst6",
            "xdg-utils",
            "libatspi2.0-0 | libatspi2.0-0t64",
            "libdrm2",
            "libgbm1",
            "libxcb-dri3-0",
            "libasound2t64 | libasound2",
            "libxkbcommon0",
            "mesa-vulkan-drivers | vulkan-icd",
          ],
          // The Tauri-era package names, so an upgrade replaces them.
          conflicts: ["puffin", "mightling-app"],
          replaces: ["puffin", "mightling-app"],
        },
      },
    },
    { name: "@electron-forge/maker-zip", platforms: ["linux", "darwin", "win32"] },
  ],
  plugins: [
    { name: "@electron-forge/plugin-auto-unpack-natives", config: {} },
    {
      name: "@electron-forge/plugin-vite",
      config: {
        build: [
          { entry: "src/early-bootstrap.ts", config: "vite.main.config.ts", target: "main" },
          { entry: "src/preload.ts", config: "vite.preload.config.ts", target: "preload" },
        ],
        renderer: [{ name: "main_window", config: "vite.renderer.config.ts" }],
      },
    },
  ],
  hooks: {
    // The version the release stamps (`MIGHTLING_VERSION`), else the package's.
    prePackage: async (forgeConfig) => {
      forgeConfig.packagerConfig.appVersion = process.env.MIGHTLING_VERSION || pkg.version;
    },
    // The Codex app's fuse settings (§3, decision 6): the binary cannot be run as Node, takes no
    // NODE_OPTIONS or --inspect, loads only its own (integrity-checked) asar, and encrypts cookies.
    postPackage: async (_forgeConfig, result) => {
      for (const outputPath of result.outputPaths) {
        // Not shipped: the SUID sandbox helper. The AppArmor profile of linux/postinst grants the
        // user namespace Chromium's sandbox uses instead, as the Codex app does.
        fs.rmSync(path.join(outputPath, "chrome-sandbox"), { force: true });
        await flipFuses(path.join(outputPath, app.executable), {
          version: FuseVersion.V1,
          resetAdHocDarwinSignature: false,
          [FuseV1Options.RunAsNode]: false,
          [FuseV1Options.EnableCookieEncryption]: true,
          [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
          [FuseV1Options.EnableNodeCliInspectArguments]: false,
          [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
          [FuseV1Options.OnlyLoadAppFromAsar]: true,
          [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
          [FuseV1Options.WasmTrapHandlers]: true,
        });
      }
    },
  },
};
