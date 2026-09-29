#!/usr/bin/env bash
# Installs Puffin on a GB10 from a checkout of this repository: the Python package (which provides
# `puffin-admin`), the workspace config, and the `puffin` terminal agent built from the pinned
# Codex fork. Everything is done by `puffin-admin`; this script only runs it in order.
#
#   ./scripts/install_gb10.sh [MODEL]     MODEL defaults to the registry's default model
set -euo pipefail

cd "$(dirname "$0")/.."
git submodule update --init codex

echo "🔧 Installing the Dreamference package (puffin-admin)..."
python3 -m pip install -e .

echo "⚙️  Writing the workspace configuration..."
if [ -n "${1:-}" ]; then
    puffin-admin init --model "$1"
else
    puffin-admin init
fi

echo "🔨 Building puffin (the terminal agent) from the codex submodule..."
puffin-admin codex build

echo "🎉 Done. Start the model server with 'puffin-admin server start', then run 'puffin'."
