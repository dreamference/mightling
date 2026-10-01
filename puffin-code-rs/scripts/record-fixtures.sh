#!/usr/bin/env bash
# Regenerates the recorded stores under tests/fixtures/code_index/stores/ from the fixture sources,
# with the real tools: codebase-memory-mcp for the graph, scip-python, rust-analyzer and
# scip-typescript for SCIP, and the scip CLI's expt-convert for the query stores. The router's tests read only the recorded
# stores, so they need none of these tools; run this after bumping one of them.
#
# Usage: record-fixtures.sh <scip-python> <scip CLI> [rust-analyzer] [codebase-memory-mcp] [scip-typescript main.js]
set -euo pipefail
SCIP_PYTHON=${1:?scip-python path}
SCIP=${2:?scip CLI path}
RUST_ANALYZER=${3:-$(ls -d "$HOME"/.rustup/toolchains/1.95.0-*/bin/rust-analyzer | head -1)}
CBM=${4:-$(command -v codebase-memory-mcp)}
SCIP_TYPESCRIPT=${5:-$(dirname "$(dirname "$SCIP_PYTHON")")/scip-typescript/dist/src/main.js}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
FIXTURE="$HERE/tests/fixtures/code_index"
WORK=$(mktemp -d /tmp/pcfix.XXXXXX)
trap 'rm -rf "$WORK" "/tmp/cbmrt-$$"' EXIT

mkdir -p "$WORK/repo" "$WORK/cache" "/tmp/cbmrt-$$" "$FIXTURE/stores"
chmod 700 "/tmp/cbmrt-$$"
cp -r "$FIXTURE/src/." "$WORK/repo/"
git -C "$WORK/repo" init -q
git -C "$WORK/repo" -c user.name=fixture -c user.email=fixture@localhost add -A
git -C "$WORK/repo" -c user.name=fixture -c user.email=fixture@localhost commit -qm fixture

# The universal layer. CBM_RUNTIME_DIR must stay short: its socket path has to fit in 108 bytes.
CBM_CACHE_DIR="$WORK/cache" CBM_RUNTIME_DIR="/tmp/cbmrt-$$" CBM_SEMANTIC_ENABLED=0 \
  "$CBM" cli --quiet index_repository "{\"repo_path\":\"$WORK/repo\"}" > /dev/null
cp "$(ls "$WORK"/cache/*.db | grep -v _config)" "$FIXTURE/stores/graph.db"
# codebase-memory writes 64 KiB pages; 4 KiB keeps the recorded copy small. The schema, and so
# its fingerprint, is unchanged.
sqlite3 "$FIXTURE/stores/graph.db" "PRAGMA page_size=4096; VACUUM;"

# The exact layers. scip-python runs with an empty environment file and no repository on PATH,
# the way puffin-code runs it (spec §6.1); rust-analyzer indexes a copy so no Cargo.lock lands here.
echo '[]' > "$WORK/env.json"
(cd "$WORK/repo" && env -i HOME="$WORK" PATH="$(dirname "$(command -v node)"):/usr/bin:/bin" \
  PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 \
  "$SCIP_PYTHON" index --quiet --project-name shapes --target-only shapes \
  --environment "$WORK/env.json" --output "$WORK/shapes.scip")
"$RUST_ANALYZER" scip "$WORK/repo/geom" --output "$WORK/geom.scip" > /dev/null 2>&1
# scip-typescript runs nothing from the project (spec §6.1). The fixture has a package.json and no
# tsconfig, so it gets the configuration puffin-code infers outside the tree (and codebase-memory,
# which does not index tsconfig.json, covers every file of it).
printf '{"compilerOptions":{"allowJs":true,"checkJs":false,"noEmit":true},"include":["%s/**/*"],"exclude":["%s/**/node_modules"]}' \
  "$WORK/repo/tsgeom" "$WORK/repo/tsgeom" > "$WORK/tsconfig.json"
env -i HOME="$WORK" PATH="$(dirname "$(command -v node)"):/usr/bin:/bin" \
  node "$SCIP_TYPESCRIPT" index "$WORK/tsconfig.json" --cwd "$WORK/repo/tsgeom" --no-progress-bar \
  --output "$WORK/tsgeom.scip" > /dev/null
# The .scip files are kept too: expt-convert does not store relationships, so puffin-code's own
# post-processing reads them from the .scip (and its tests do the same on these copies).
for name in shapes geom tsgeom; do
  rm -f "$FIXTURE/stores/$name.db"
  "$SCIP" expt-convert "$WORK/$name.scip" --output "$FIXTURE/stores/$name.db" > /dev/null
  cp "$WORK/$name.scip" "$FIXTURE/stores/$name.scip"
done
ls -la "$FIXTURE/stores"
