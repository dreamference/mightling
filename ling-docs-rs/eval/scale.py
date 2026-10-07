"""Query latency at the spec's acceptance size: a 50k-chunk collection, built by repeating the real chunk
set eight times (FTS5) and tiling each model's real vectors with small noise (dense). One BLAS thread, as a
query process would have. Writes results/scale.json."""
import os

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
import glob  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

base = json.load(open(f"{D}/chunks/pdfium-512.json"))["chunks"]
questions = [json.loads(line) for line in open(f"{D}/questions.jsonl")]
REP = -(-50_000 // len(base))
big = [c for _ in range(REP) for c in base][:50_000]
t = time.perf_counter()
bm = evallib.BM25(big)
fts_build = time.perf_counter() - t


def pct(xs):
    return {"p50": round(float(np.percentile(xs, 50)), 2), "p95": round(float(np.percentile(xs, 95)), 2)}


bm_lat, bm_ranked = [], []
for q in questions:
    t = time.perf_counter()
    bm_ranked.append(bm.search(q["question"]))
    bm_lat.append((time.perf_counter() - t) * 1000)
out = {"chunks": len(big), "fts_build_seconds": round(fts_build, 1), "bm25_ms": pct(bm_lat), "models": {}}
rng = np.random.default_rng(0)
for info_path in sorted(glob.glob(f"{D}/emb/pdfium-512__*.json")):
    info = json.load(open(info_path))
    prefix = info_path[:-5]
    v = np.load(f"{prefix}.docs.npy")
    tiled = np.tile(v, (REP, 1))[:50_000]
    tiled = tiled + rng.normal(0, 0.01, tiled.shape).astype(np.float32)
    dense = evallib.Dense(tiled)
    qv = np.load(f"{prefix}.queries.npy")
    d_lat, total = [], []
    for i, q in enumerate(qv):
        t = time.perf_counter()
        d = dense.search(q)
        evallib.rrf(bm_ranked[i], d)
        d_lat.append((time.perf_counter() - t) * 1000)
    # End to end = query embedding (measured in embed.py, 4 threads) + BM25 + exact dense + fusion.
    e2e = [info["query_ms_p95"] + b + x for b, x in zip(sorted(bm_lat), sorted(d_lat))]
    out["models"][info["model"]] = {"dim": info["dim"], "dense_plus_fusion_ms": pct(d_lat),
                                    "query_embed_ms_p95": round(info["query_ms_p95"], 1),
                                    "end_to_end_ms_p95_upper": round(float(np.percentile(e2e, 95)), 1),
                                    "vector_mb": round(tiled.nbytes / 2**20)}
json.dump(out, open(f"{D}/results/scale.json", "w"), indent=1)
print(json.dumps(out, indent=1))
