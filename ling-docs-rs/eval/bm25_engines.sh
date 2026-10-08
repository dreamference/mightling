#!/bin/bash
# BM25 on each PDF engine's 512-token chunks: only the PDF questions differ between them.
D="$(dirname "$(readlink -f "$0")")"
for e in pdfium pdfium-ordered pymupdf pymupdf-sort pdftotext; do
  nice -n 10 "$D/venv/bin/python" "$D/evaluate.py" "$D/chunks/$e-512.json" 2>/dev/null | head -1 | \
    "$D/venv/bin/python" -c "import json,sys; r=json.loads(sys.stdin.readline()); print(json.dumps({'engine': '$e', 'ceiling': r['ceiling'], 'recall10': round(r['recall10'],3), 'pdf': r['by_format']['pdf'], 'mrr10': round(r['mrr10'],3)}))"
done | tee "$D/results/bm25-pdf-engines.jsonl"
