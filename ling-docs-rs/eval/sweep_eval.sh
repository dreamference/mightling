#!/bin/bash
# One model across the chunk sizes: R@10, MRR and the per-group counts. Usage: sweep_eval.sh <model-basename>
D="$(dirname "$(readlink -f "$0")")"
for t in 256 384 512 768 1024; do
  [ -f "$D/emb/pdfium-ordered-${t}__$1.json" ] || continue
  nice "$D/venv/bin/python" "$D/evaluate.py" "$D/chunks/pdfium-ordered-$t.json" "$D/emb/pdfium-ordered-${t}__$1" 2>/dev/null | \
    "$D/venv/bin/python" -c "
import json, sys
for l in sys.stdin:
    r = json.loads(l)
    if r['method'].startswith(('hybrid', 'dense')):
        print($t, r['n_chunks'], r['method'].split(':')[0], round(r['recall10'], 3), round(r['recall5'], 3), round(r['mrr10'], 3), r['ceiling'], r.get('embed_chunks_per_second'), r['by_group'])"
done
