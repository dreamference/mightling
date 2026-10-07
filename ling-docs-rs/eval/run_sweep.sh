#!/bin/bash
# Chunk-size sweep: the given models on the 256- and 768-token PDFium chunks (512 is run_embed_all.sh).
# Usage: run_sweep.sh <model> [<model> …]
D="$(dirname "$(readlink -f "$0")")"
for m in "$@"; do
  for tag in pdfium-256 pdfium-768; do
    bash "$D/run_embed.sh" "$m" "$tag" 2>&1 | grep -v -i warning | tail -1
  done
done
