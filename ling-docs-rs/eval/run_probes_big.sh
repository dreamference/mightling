#!/bin/bash
# The candidates that passed the 4 GB cap at batch 16 (arctic-m-v2.0 fp32, Qwen3-Embedding-0.6B) or
# could not load from the hub cache (bge-m3), probed again at batch 4.
D="$(dirname "$(readlink -f "$0")")"
export BATCH=4
for m in Snowflake/snowflake-arctic-embed-m-v2.0 Qwen/Qwen3-Embedding-0.6B-Q; do
  bash "$D/run_embed.sh" "$m" pdfium-ordered-512 160 2>&1 | grep -v -i warn | tail -1 | cut -c1-400
done
MODEL_DIR="$D/models/bge-m3-plain" bash "$D/run_embed.sh" BAAI/bge-m3 pdfium-ordered-512 160 2>&1 | grep -v -i warn | tail -1 | cut -c1-400
