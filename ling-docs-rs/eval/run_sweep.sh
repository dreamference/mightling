#!/bin/bash
# Chunk-size sweep: the given models on the 256-, 384-, 768- and 1024-token chunks of the ordered PDFium
# text (512 is run_embed_all.sh). Usage: [CPUS=15-18] run_sweep.sh <model> [<model> …]
D="$(dirname "$(readlink -f "$0")")"
for m in "$@"; do
  for t in 256 384 768 1024; do
    bash "$D/run_embed.sh" "$m" "pdfium-ordered-$t" 2>&1 | grep -v -i warning | tail -1 | cut -c1-300
  done
done
