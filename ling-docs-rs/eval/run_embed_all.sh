#!/bin/bash
# Every candidate that fits the 4 GB cap on the 512-token chunks (ordered PDFium, XLM-R counts), one at a time.
D="$(dirname "$(readlink -f "$0")")"
for m in minishlab/potion-multilingual-128M sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
         intfloat/multilingual-e5-small-int8 ibm-granite/granite-embedding-107m-multilingual \
         intfloat/multilingual-e5-small intfloat/multilingual-e5-base Snowflake/snowflake-arctic-embed-m-v2.0-int8; do
  bash "$D/run_embed.sh" "$m" pdfium-ordered-512 2>&1 | grep -v -i warn | tail -1 | cut -c1-300
done
