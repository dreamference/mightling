#!/bin/bash
# Chunk the corpus: PDFium at three sizes, every other PDF engine at 512.
D="$(dirname "$(readlink -f "$0")")"
export HF_HOME="$D/hf"
for t in 512 256 768; do
  nice -n 15 ionice -c 3 "$D/venv/bin/python" "$D/build_chunks.py" pdfium "$t"
done
for e in pymupdf pymupdf-sort pdftotext pdf_oxide pdf-extract pypdf pdfminer; do
  nice -n 15 ionice -c 3 "$D/venv/bin/python" "$D/build_chunks.py" "$e" 512
done
