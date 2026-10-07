#!/usr/bin/env bash
# Packages the installed puffin binaries as release assets for one target:
#
#   scripts/package_puffin.sh <target> <bin dir> <out dir>
#
# Writes <name>-<target>.gz for puffin, codex-code-mode-host, puffin-search, puffin-fetch and
# puffin-code, and puffin-<target>.sha256sums covering them: the names and the checksum file that
# install.sh and `puffin update` (puffin-rs/src/update.rs) look for. Portable between Linux and
# macOS (sha256sum or shasum).
set -euo pipefail

target="${1:?target triple}"
bin="${2:?bin directory}"
out="${3:?output directory}"

mkdir -p "$out"
names=(puffin codex-code-mode-host puffin-search puffin-fetch puffin-code)
files=()
for name in "${names[@]}"; do
    [ -x "$bin/$name" ] || { echo "missing $bin/$name" >&2; exit 1; }
    gzip -9 -c "$bin/$name" > "$out/$name-$target.gz"
    files+=("$name-$target.gz")
done

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$out" && sha256sum "${files[@]}" > "puffin-$target.sha256sums")
else
    (cd "$out" && shasum -a 256 "${files[@]}" > "puffin-$target.sha256sums")
fi
ls -l "$out"
