"""List the questions a method misses at 10: misses.py <chunks.json> [<embedding-prefix>]."""
import json
import os
import sys

import numpy as np

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

data = json.load(open(sys.argv[1]))
chunks = data["chunks"]
qs = [json.loads(line) for line in open(f"{D}/questions.jsonl")]
bm = evallib.BM25(chunks)
bm_r = [bm.search(q["question"]) for q in qs]
d_r = None
if len(sys.argv) > 2:
    dense = evallib.Dense(np.load(sys.argv[2] + ".docs.npy"))
    d_r = [dense.search(v) for v in np.load(sys.argv[2] + ".queries.npy")]


def rank(r, q):
    return next((i for i, c in enumerate(r[:10]) if chunks[c]["doc"] == q["doc"] and evallib.is_hit(chunks[c]["text"], q["snippet"])), None)


for i, q in enumerate(qs):
    b = rank(bm_r[i], q)
    d = rank(d_r[i], q) if d_r else None
    h = rank(evallib.rrf(bm_r[i], d_r[i]), q) if d_r else None
    if b is None or (d_r and (d is None or h is None)):
        print(f"{q['id']} {q['kind'][:4]} bm25={b} dense={d} hybrid={h} | {q['question'][:80]}")
