#!/usr/bin/env bash
set -e

MODEL="${1:-qwen2.5-coder-32b}"
PORT="${2:-8000}"

echo "=== Launching vLLM Server Optimized for NVIDIA GB10 (128GB Unified Memory) ==="
echo "Target Model: $MODEL"
echo "API Port:     $PORT"

python3 -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 \
    --port "$PORT" \
    --model "$MODEL" \
    --max-model-len 16384 \
    --gpu-memory-utilization 0.90 \
    --trust-remote-code \
    --enforce-eager
