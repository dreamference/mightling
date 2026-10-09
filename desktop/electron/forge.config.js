// Electron Forge, the way the Codex desktop app is packaged (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md
// §3): Vite builds the main process, the preload and Work's UI into `.vite/`; `package` writes
// `out/Mightling-linux-<arch>/`; `make` wraps that in a `.deb` (and a zip). The fuses are flipped on
// the packaged binary. `ling` and `codex-code-mode-host` ride along as extra resources, copied into
// `resources/` by `ling-admin desktop build` before `make` runs.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

// @electron/fuses 2 (Forge 7's own fuses plugin pinned the 1.x line, which has no WasmTrapHandlers
// fuse; Forge 8's takes 2.x, but the hook also removes chrome-sandbox and re-signs on a Mac), flipped
// on the packaged binary in the postPackage hook below.
const { FuseV1Options, FuseVersion, flipFuses } = require("@electron/fuses");

const app = require("./app.json");
const { MakerDebOwnRelations } = require("./linux/deb-maker");
const pkg = require("./package.json");

const resources = path.join(__dirname, "resources");
const extraResource = ["ling", "codex-code-mode-host", "rg"]
  .map((name) => path.join(resources, name))
  .filter((file) => fs.existsSync(file));

// What `Mightling --version` prints for the bundled `ling` (src/version.ts, LING_VERSION_FILE):
// `ling --version`'s first line, read once here, at build time, so the app never runs `ling` to
// answer it. Run with a scratch HOME and CODEX_HOME, so it creates or migrates no real home folder.
// A bundled `ling` whose version cannot be read stops the build.
function stampLingVersion(packagerConfig) {
  const ling = path.join(resources, "ling");
  const stamp = path.join(resources, "ling.version");
  fs.rmSync(stamp, { force: true });
  if (!fs.existsSync(ling)) return;
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "mightling-ling-version-"));
  try {
    const output = execFileSync(ling, ["--version"], {
      env: { ...process.env, HOME: scratch, CODEX_HOME: scratch },
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 60_000,
    });
    const line = output.split("\n")[0].trim();
    if (!line) throw new Error(`${ling} --version printed nothing`);
    fs.writeFileSync(stamp, `${line}\n`);
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
  packagerConfig.extraResource = [...(packagerConfig.extraResource || []).filter((file) => file !== stamp), stamp];
}

// The Mac preview (§10): the packager wants an `.icns`, which `generateAssets` below makes from
// icons/icon.png with the system's own `sips` and `iconutil`, into the git-ignored `resources/`
// (so it stays out of the asar, which carries icons/ for the tray).
const darwin = process.platform === "darwin";

module.exports = {
  packagerConfig: {
    name: app.productName,
    executableName: app.executable,
    appBundleId: app.identifier,
    appCopyright: "Copyright (C) 2026 Dreamference contributors. AGPL-3.0-or-later.",
    asar: true,
    icon: darwin ? path.join(resources, "icon") : path.join(__dirname, "icons", "icon"),
    extraResource,
    // macOS only (the packager ignores these elsewhere). The scheme, so `mightling://` links reach
    // `open-url`; the category; and the Local Network entries macOS 15 asks about before the app
    // (or the bundled `ling` it starts) may browse for a node or connect to one on the LAN.
    protocols: [{ name: app.productName, schemes: [app.scheme] }],
    appCategoryType: "public.app-category.developer-tools",
    extendInfo: {
      NSLocalNetworkUsageDescription:
        "Mightling finds your Mightling node (a GB10) on the local network and talks to its model server and web UI.",
      // ling-rs/node-locator's SERVICE_TYPE, without the domain: the bundled `ling` browses for it.
      NSBonjourServices: ["_mightling-node._tcp"],
    },
    // The asar holds what the app loads: the Vite build (main, preload, Work's page), the icons
    // and package.json. Sources, configs, tests, the staged
    // binaries (extra resources above) and node_modules stay out.
    ignore: (file) =>
      !(file === "" || file === "/package.json" || file === "/.vite" || file.startsWith("/.vite/") || file === "/icons" || file.startsWith("/icons/")),
  },
  rebuildConfig: {},
  makers: [
    // Forge's maker-deb, declaring only the relationships listed here (linux/deb-maker.js).
    new MakerDebOwnRelations({
      options: {
        name: app.packageName,
        productName: app.productName,
        genericName: "AI assistant",
        description: "Private AI on your GB10: a coding agent and a chat assistant, nothing leaves your machine.",
        productDescription:
          "Mightling's desktop app: Ask (questions with no project) and Work (the coding agent on your projects) in one window, on ling, which is bundled inside the app.",
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
        // The whole Depends line: nothing is added to it (linux/deb-maker.js), and there is no
        // Recommends or Suggests. Chromium's own libraries (electron-installer-debian's list for
        // this Electron, each with its 64-bit-time name where Ubuntu 24.04 renamed it), then the
        // sound library, xkbcommon and Vulkan. Left out of the installer's list: libsecret-1-0,
        // which Chromium only dlopens to keep the cookie key in the desktop keyring and does
        // without (the app stores no credential), and the trash helpers (it trashes nothing).
        // None of the Codex app's extras (its TPM and USB libraries).
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
        // The Tauri-era package names, so an upgrade replaces them (written into the control
        // file by linux/deb-maker.js: electron-installer-debian's template has neither field).
        conflicts: ["puffin", "mightling-app"],
        replaces: ["puffin", "mightling-app"],
      },
    }),
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
    // The Mac icon (see `darwin` above); nothing to do elsewhere or when it is already there.
    generateAssets: async () => {
      if (!darwin || fs.existsSync(path.join(resources, "icon.icns"))) return;
      const { execFileSync } = require("node:child_process");
      const iconset = path.join(resources, "icon.iconset");
      fs.rmSync(iconset, { recursive: true, force: true });
      fs.mkdirSync(iconset, { recursive: true });
      const source = path.join(__dirname, "icons", "icon.png");
      for (const size of [16, 32, 128, 256, 512]) {
        for (const [scale, suffix] of [[1, ""], [2, "@2x"]]) {
          const pixels = String(Math.min(size * scale, 512));
          execFileSync("sips", ["-z", pixels, pixels, source, "--out", path.join(iconset, `icon_${size}x${size}${suffix}.png`)], { stdio: "ignore" });
        }
      }
      execFileSync("iconutil", ["-c", "icns", iconset, "-o", path.join(resources, "icon.icns")]);
      fs.rmSync(iconset, { recursive: true, force: true });
    },
    // The version the release stamps (`MIGHTLING_VERSION`), else the package's; and the bundled
    // `ling`'s, for `--version` (stampLingVersion above).
    prePackage: async (forgeConfig) => {
      forgeConfig.packagerConfig.appVersion = process.env.MIGHTLING_VERSION || pkg.version;
      stampLingVersion(forgeConfig.packagerConfig);
    },
    // The Codex app's fuse settings (§3, decision 6): the binary cannot be run as Node, takes no
    // NODE_OPTIONS or --inspect, loads only its own (integrity-checked) asar, and encrypts cookies.
    postPackage: async (_forgeConfig, result) => {
      for (const outputPath of result.outputPaths) {
        // Not shipped: the SUID sandbox helper. The AppArmor profile of linux/postinst grants the
        // user namespace Chromium's sandbox uses instead, as the Codex app does.
        fs.rmSync(path.join(outputPath, "chrome-sandbox"), { force: true });
        // On a Mac the fuses live in the bundle (fuses finds the framework from the `.app`), and
        // flipping them breaks the ad-hoc signature Apple silicon will not run without, so fuses
        // re-signs it ad hoc; the workflow then signs the whole bundle ad hoc (§10).
        const binary = darwin ? path.join(outputPath, `${app.productName}.app`) : path.join(outputPath, app.executable);
        await flipFuses(binary, {
          version: FuseVersion.V1,
          resetAdHocDarwinSignature: darwin,
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
