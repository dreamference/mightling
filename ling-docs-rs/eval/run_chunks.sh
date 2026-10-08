#!/bin/bash
# Chunk the corpus (multilingual part included) with the XLM-R tokenizer: the chosen PDF pass at the
# sweep's sizes, and every PDF engine at 512 for the extractor comparison.
D="$(dirname "$(readlink -f "$0")")"
export HF_HOME="$D/hf" HF_HUB_OFFLINE=1
for t in 512 256 384 768 1024; do
  nice -n 15 ionice -c 3 "$D/venv/bin/python" "$D/build_chunks.py" pdfium-ordered "$t"
done
for e in pdfium pymupdf pymupdf-sort pdftotext; do
  nice -n 15 ionice -c 3 "$D/venv/bin/python" "$D/build_chunks.py" "$e" 512
done
