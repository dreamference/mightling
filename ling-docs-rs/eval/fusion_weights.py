"""Weighted RRF: does down-weighting BM25 keep dense's recall and hybrid's precision?
fusion_weights.py <chunks.json> <embedding-prefix> -> one line per BM25 weight (dense weight 1)."""
import json
import os
import sys

import numpy as np

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

data = json.load(open(sys.argv[1]))
chunks = data["chunks"]
questions = [json.loads(line) for line in open(f"{D}/questions.jsonl")]
bm = evallib.BM25(chunks, trigram=True)
b_ranked = [bm.search(q["question"]) for q in questions]
dense = evallib.Dense(np.load(f"{sys.argv[2]}.docs.npy"))
d_ranked = [dense.search(v) for v in np.load(f"{sys.argv[2]}.queries.npy")]


def wrrf(b, d, wb, k=60, top=100):
    score = {}
    for w, r in ((wb, b), (1.0, d)):
        for rank, i in enumerate(r):
            score[i] = score.get(i, 0.0) + w / (k + rank + 1)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])][:top]


out = []
for wb in (0.0, 0.25, 0.5, 0.75, 1.0):
    m = evallib.metrics([wrrf(b, d, wb) for b, d in zip(b_ranked, d_ranked)], questions, chunks)
    row = {"bm25_weight": wb, "recall10": round(m["recall10"], 3), "recall5": round(m["recall5"], 3),
           "mrr10": round(m["mrr10"], 3), "by_group": m["by_group"]}
    out.append(row)
    print(json.dumps(row))
tag = os.path.splitext(os.path.basename(sys.argv[1]))[0].replace("pdfium-ordered-", "")
json.dump(out, open(f"{D}/results/fusion-weights-{tag}.json", "w"), indent=1)
