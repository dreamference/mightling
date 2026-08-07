#!/usr/bin/env bash
set -e

echo "=== Installing DGXCoder & Goose Agent Runtime on NVIDIA GB10 ==="

# Check for goose CLI
if ! command -v goose &> /dev/null; then
    echo "📦 Installing Goose CLI (aaif-goose/goose)..."
    curl -fsSL https://github.com/aaif-goose/goose/releases/latest/download/download_cli.sh | sh || {
        echo "⚠️ Direct script download failed. Installing goose via pip..."
        pip install goose-ai || true
    }
else
    echo "✅ Goose CLI is already installed."
fi

# Install python package in editable mode with dependencies
pip install -e .

echo "🚀 Initializing DGXCoder Goose configuration & indexing workspace..."
dgxcoder init --model "${1:-qwen3.6-35b-a3b-nvfp4}"

echo "🎉 DGXCoder Installation & GB10 Setup Complete!"
