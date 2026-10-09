// The `.deb` maker: Forge's `maker-deb`, except that the package declares exactly the relationships
// forge.config.js lists, and nothing else.
//
// `electron-installer-debian` (which `maker-deb` drives) always adds its own defaults to ours: a
// `Depends` on `libsecret-1-0`, a trash helper (`kde-cli-tools | kde-runtime | trash-cli |
// libglib2.0-bin | gvfs`) and a bare `libgtk-3-0`, plus a `Recommends` and a `Suggests`; it has no
// option to turn that off. It also has no `Conflicts`/`Replaces` fields at all: its control template
// drops them. So the installer is subclassed here: the merged lists are replaced by ours after its own
// option handling (name sanitising, description wrapping and the rest are kept), and the two fields
// are written into the control file. `make` replays the installer's own sequence (its default export
// builds a private class, so a subclass cannot be passed to it). The installer is maker-deb's optional
// dependency and is not installed on Windows, so it is required only when a `.deb` is made.
const fs = require("node:fs/promises");
const path = require("node:path");

const { MakerDeb, debianArch } = require("@electron-forge/maker-deb");

/** The package relationships the installer would otherwise merge with its defaults. */
const MERGED_FIELDS = ["depends", "recommends", "suggests", "enhances", "preDepends"];

/** The relationships its control template leaves out, as option name and control field. */
const EXTRA_FIELDS = [
  ["conflicts", "Conflicts"],
  ["replaces", "Replaces"],
];

/** `electron-installer-debian`'s default file name (not exported). */
const RENAME = (dest) =>
  path.join(dest, "<%= name %>_<%= version %><% if (revision) { %>-<%= revision %><% } %>_<%= arch %>.deb");

let ownRelationsInstallerClass = null;

/** `electron-installer-debian`'s installer, declaring only the relationships it is given. */
function ownRelationsInstaller() {
  if (ownRelationsInstallerClass) return ownRelationsInstallerClass;
  const { Installer } = require("electron-installer-debian");
  class OwnRelationsInstaller extends Installer {
    generateOptions() {
      super.generateOptions();
      const ours = this.userSupplied.options || {};
      for (const field of MERGED_FIELDS) this.options[field] = [...(ours[field] || [])];
      return this.options;
    }

    async createControl() {
      await super.createControl();
      const lines = EXTRA_FIELDS.filter(([option]) => (this.options[option] || []).length > 0).map(
        ([option, field]) => `${field}: ${this.options[option].join(", ")}\n`,
      );
      if (lines.length === 0) return;
      const control = path.join(this.stagingDir, "DEBIAN", "control");
      const text = await fs.readFile(control, "utf8");
      // Before `Description`, whose continuation lines end the paragraph.
      const at = text.search(/^Description:/m);
      await fs.writeFile(control, at < 0 ? text + lines.join("") : text.slice(0, at) + lines.join("") + text.slice(at));
    }
  }
  ownRelationsInstallerClass = OwnRelationsInstaller;
  return OwnRelationsInstaller;
}

class MakerDebOwnRelations extends MakerDeb {
  async make({ dir, makeDir, targetArch }) {
    const dest = path.resolve(makeDir, "deb", targetArch);
    await this.ensureDirectory(dest);
    const Installer = ownRelationsInstaller();
    const installer = new Installer({
      options: {},
      ...this.config,
      arch: debianArch(targetArch),
      src: dir,
      dest,
      rename: RENAME,
      logger: () => {},
    });
    await installer.generateDefaults();
    await installer.generateOptions();
    await installer.createStagingDir();
    await installer.createContents();
    await installer.createPackage();
    await installer.movePackage();
    return installer.options.packagePaths;
  }
}

module.exports = { MakerDebOwnRelations, ownRelationsInstaller };
