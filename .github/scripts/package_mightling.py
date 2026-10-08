"""Packages the installed ling binaries as release assets: one `.gz` per command and a checksum file.

    python .github/scripts/package_mightling.py <dist-dir>

Reads the binaries CodexBrandedBuilder installed, names each `<command>-<target>.gz` (on Windows
the archive holds `<command>.exe`), and writes `ling-<target>.sha256sums` covering all of them,
which `ling update`, install.sh and install.ps1 check every archive against. Optional commands
are packaged when they were built. Used by .github/workflows/windows.yml.
"""

import gzip
import hashlib
import os
import shutil
import sys

from dreamference.runner.codex_branded_builder import EXE_SUFFIX, IS_WINDOWS, CodexBrandedBuilder

REQUIRED = ("ling", "codex-code-mode-host")
OPTIONAL = ("ling-search", "ling-fetch", "ling-code")
WINDOWS_ONLY = ("codex-windows-sandbox-setup", "codex-command-runner")


def main() -> None:
    dist = sys.argv[1]
    os.makedirs(dist, exist_ok=True)
    target = CodexBrandedBuilder.host_target()
    bin_dir = os.path.dirname(CodexBrandedBuilder.executable_path())
    names = list(REQUIRED) + list(OPTIONAL) + (list(WINDOWS_ONLY) if IS_WINDOWS else [])
    packaged = []
    for name in names:
        binary = os.path.join(bin_dir, name + EXE_SUFFIX)
        if not os.path.isfile(binary):
            if name in REQUIRED:
                sys.exit(f"missing {binary}")
            print(f"skipping {name}: not built")
            continue
        asset = f"{name}-{target}.gz"
        with open(binary, "rb") as source, gzip.open(os.path.join(dist, asset), "wb", compresslevel=9) as out:
            shutil.copyfileobj(source, out)
        packaged.append(asset)
    with open(os.path.join(dist, f"ling-{target}.sha256sums"), "w", newline="\n") as sums:
        for asset in packaged:
            with open(os.path.join(dist, asset), "rb") as handle:
                sums.write(f"{hashlib.sha256(handle.read()).hexdigest()}  {asset}\n")
    print("\n".join(sorted(os.listdir(dist))))


if __name__ == "__main__":
    main()
