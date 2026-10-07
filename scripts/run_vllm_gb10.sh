#!/usr/bin/env bash
# Starts the model server. A thin wrapper over `mling-admin server start`, which applies the
# model's registry recipe and the host-safety checks and memory-pressure watchdog that keep a
# model load on GB10's unified memory from freezing the whole machine. This script used to launch
# vLLM directly, skipping all of that.
#
#   ./scripts/run_vllm_gb10.sh [MODEL] [PORT] [DRAFT_MODEL] [NUM_SPECULATIVE_TOKENS]
set -euo pipefail

args=(server start)
[ -n "${1:-}" ] && args+=(--model "$1")
[ -n "${2:-}" ] && args+=(--port "$2")
[ -n "${3:-}" ] && args+=(--draft-model "$3")
[ -n "${4:-}" ] && args+=(--num-speculative-tokens "$4")
exec mling-admin "${args[@]}"
