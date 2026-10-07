#!/bin/bash
# Every candidate on the 512-token PDFium chunks, one at a time.
D="$(dirname "$(readlink -f "$0")")"
for m in minishlab/potion-retrieval-32M BAAI/bge-small-en-v1.5 snowflake/snowflake-arctic-embed-s \
         nomic-ai/nomic-embed-text-v1.5-Q nomic-ai/nomic-embed-text-v1.5; do
  bash "$D/run_embed.sh" "$m" pdfium-512 2>&1 | grep -v -i warning | tail -2
done
