#!/usr/bin/env bash
# Installs Puffin on a GB10 from a checkout of this repository: the Python package (which provides
# `puffin-admin`), the workspace config, the host settings a model load needs, and the `puffin`
# terminal agent built from the pinned Codex fork. Everything is done by `puffin-admin`; this
# script only runs it in order. To install from a release without a checkout, use ../install.sh.
#
#   ./scripts/install_gb10.sh [MODEL]     MODEL defaults to the registry's default model
set -euo pipefail

cd "$(dirname "$0")/.."
git submodule update --init codex

echo "🔧 Installing Puffin's admin package (puffin-admin)..."
python3 -m pip install -e .

echo "⚙️  Writing the workspace configuration..."
if [ -n "${1:-}" ]; then
    puffin-admin init --model "$1"
else
    puffin-admin init
fi

echo "🛡️  Checking the host settings a model load needs (swap, sysctls, earlyoom, sysstat)..."
# Applies what `server start` would otherwise refuse over; sudo asks before anything changes.
puffin-admin host setup || echo "⚠️  Fix the above before 'puffin-admin server start'."

echo "🔨 Building puffin (the terminal agent) from the codex submodule..."
puffin-admin codex build

echo "🎉 Done. Start the model server with 'puffin-admin server start', then run 'puffin'."
