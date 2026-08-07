#!/usr/bin/env bash
set -e

MODEL="${1:-qwen3.6-35b-a3b-nvfp4}"
PORT="${2:-8000}"
DRAFT_MODEL="${3:-}"
NUM_TOKENS="${4:-5}"

echo "=== Launching vLLM Server Optimized for NVIDIA GB10 (128GB Unified Memory) ==="
echo "Target Model: $MODEL"
echo "API Port:     $PORT"

CMD=(
    python3 -m vllm.entrypoints.openai.api_server
    --host 0.0.0.0
    --port "$PORT"
    --model "$MODEL"
    --max-model-len 16384
    --gpu-memory-utilization 0.50
    --trust-remote-code
    --enforce-eager
)

if [ -n "$DRAFT_MODEL" ]; then
    echo "Speculative Draft Model: $DRAFT_MODEL ($NUM_TOKENS tokens)"
    CMD+=(--speculative-model "$DRAFT_MODEL" --num-speculative-tokens "$NUM_TOKENS")
fi

"${CMD[@]}"
