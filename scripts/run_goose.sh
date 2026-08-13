#!/usr/bin/env bash
set -e

export GOOSE_PROVIDER="openai"
export OPENAI_HOST="${DREAMFERENCE_VLLM_HOST:-http://localhost:8000}"
export OPENAI_BASE_PATH="v1"
export OPENAI_API_KEY="gb10-local-token"
export GOOSE_MODEL="${DREAMFERENCE_MODEL:-qwen3.6-35b-a3b-nvfp4}"

echo "🚀 Starting Goose session connected to GB10 local endpoint ($GOOSE_MODEL)..."
goose session "$@"
