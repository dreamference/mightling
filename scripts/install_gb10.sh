#!/usr/bin/env bash
set -e

# ANSI color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${BLUE}${BOLD}=== 🚀 Installing Dreamference & Goose Agent Runtime on NVIDIA GB10 ===${NC}"

# Check for goose CLI
if ! command -v goose &> /dev/null; then
    echo -e "📦 ${BLUE}Installing Goose CLI (aaif-goose/goose)...${NC}"
    curl -fsSL https://github.com/aaif-goose/goose/releases/latest/download/download_cli.sh | sh || {
        echo -e "⚠️ ${BLUE}Direct script download failed. Installing goose via pip...${NC}"
        pip install goose-ai || true
    }
else
    echo -e "✅ ${GREEN}Goose CLI is already installed.${NC}"
fi

# Install python package in editable mode with dependencies
echo -e "🔧 ${BLUE}Installing Dreamference package...${NC}"
pip install -e .

echo -e "⚙️ ${BLUE}Initializing Dreamference configuration & indexing workspace...${NC}"
dream init --model "${1:-qwen3.6-35b-a3b-nvfp4}"

echo -e "\n${GREEN}${BOLD}🎉 Dreamference Installation & GB10 Setup Complete!${NC}"
echo -e "👉 Run ${BOLD}'dream chat'${NC} to start your first session."
