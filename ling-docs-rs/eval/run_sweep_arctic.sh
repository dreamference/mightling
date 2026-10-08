#!/bin/bash
# The chosen model at two more chunk sizes, at batch 1 (its fastest and smallest setting, §15.3):
# 256, and 768, which arctic-embed-m-v2.0 reads whole (8,192-token window) where granite and e5 cut it.
D="$(dirname "$(readlink -f "$0")")"
export BATCH=1
for t in 768 256; do
  bash "$D/run_embed.sh" Snowflake/snowflake-arctic-embed-m-v2.0-int8 "pdfium-ordered-$t" 2>&1 | grep -v -i warning | tail -1 | cut -c1-300
done
