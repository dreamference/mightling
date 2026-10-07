#!/bin/bash
# Throughput probe: every candidate on the same 160 chunks (spread over the 512-token set, all languages)
# plus the 181 questions, one at a time on the same four X925 cores.
D="$(dirname "$(readlink -f "$0")")"
for m in minishlab/potion-multilingual-128M sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
         intfloat/multilingual-e5-small intfloat/multilingual-e5-small-int8 ibm-granite/granite-embedding-107m-multilingual \
         intfloat/multilingual-e5-base Snowflake/snowflake-arctic-embed-m-v2.0 Snowflake/snowflake-arctic-embed-m-v2.0-int8 \
         BAAI/bge-m3 Qwen/Qwen3-Embedding-0.6B-Q; do
  bash "$D/run_embed.sh" "$m" pdfium-ordered-512 160 2>&1 | grep -v -i warn | tail -1 | cut -c1-400
done
