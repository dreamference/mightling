#!/usr/bin/env bash
# Packages the installed ling binaries as release assets for one target:
#
#   scripts/package_mightling.sh <target> <bin dir> <out dir>
#
# Writes <name>-<target>.gz for ling, codex-code-mode-host, ling-search, ling-fetch and
# ling-code, and ling-<target>.sha256sums covering them: the names and the checksum file that
# install.sh and `ling update` (ling-rs/src/update.rs) look for. Portable between Linux and
# macOS (sha256sum or shasum).
set -euo pipefail

target="${1:?target triple}"
bin="${2:?bin directory}"
out="${3:?output directory}"

mkdir -p "$out"
names=(ling codex-code-mode-host ling-search ling-fetch ling-code)
files=()
for name in "${names[@]}"; do
    [ -x "$bin/$name" ] || { echo "missing $bin/$name" >&2; exit 1; }
    gzip -9 -c "$bin/$name" > "$out/$name-$target.gz"
    files+=("$name-$target.gz")
done

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$out" && sha256sum "${files[@]}" > "ling-$target.sha256sums")
else
    (cd "$out" && shasum -a 256 "${files[@]}" > "ling-$target.sha256sums")
fi
ls -l "$out"
