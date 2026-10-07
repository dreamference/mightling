#!/usr/bin/env bash
# Installs Mightling on a GB10 from a checkout of this repository: the Python package (which provides
# `mling-admin`), the workspace config, the host settings a model load needs, and the `mling`
# terminal agent built from the pinned Codex fork. Everything is done by `mling-admin`; this
# script only runs it in order. To install from a release without a checkout, use ../install.sh.
#
#   ./scripts/install_gb10.sh [MODEL]     MODEL defaults to the registry's default model
set -euo pipefail

cd "$(dirname "$0")/.."
git submodule update --init codex

echo "🔧 Installing Mightling's admin package (mling-admin)..."
python3 -m pip install -e .

echo "⚙️  Writing the workspace configuration..."
if [ -n "${1:-}" ]; then
    mling-admin init --model "$1"
else
    mling-admin init
fi

echo "🛡️  Checking the host settings a model load needs (swap, sysctls, earlyoom, sysstat)..."
# Applies what `server start` would otherwise refuse over; sudo asks before anything changes.
mling-admin host setup || echo "⚠️  Fix the above before 'mling-admin server start'."

echo "🔨 Building mling (the terminal agent) from the codex submodule..."
mling-admin codex build

echo "🎉 Done. Start the model server with 'mling-admin server start', then run 'mling'."
