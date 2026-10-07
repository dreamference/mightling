#!/bin/bash
# BM25 on each PDF engine's 512-token chunks: the PDF questions only differ between them.
D="$(dirname "$(readlink -f "$0")")"
for e in pdfium pymupdf pymupdf-sort pdftotext pdf_oxide pdf-extract pypdf pdfminer; do
  nice -n 10 "$D/venv/bin/python" "$D/evaluate.py" "$D/chunks/$e-512.json" | \
    "$D/venv/bin/python" -c "import json,sys; r=json.loads(sys.stdin.readline()); print(r['chunks'], 'ceiling', r['ceiling'], 'R@10', round(r['recall10'],3), 'pdf', r['by_format']['pdf'], 'MRR', round(r['mrr10'],3))"
done
