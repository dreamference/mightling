"""Score retrieval: evaluate.py <chunks.json> [<embedding-prefix> ...] -> one JSON line per method on stdout.
Methods: bm25 (SQLite FTS5), dense:<model> (exact cosine), hybrid:<model> (RRF k=60 of the two top-100s)."""
import json
import os
import sys
import time

import numpy as np

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

chunks_path, prefixes = sys.argv[1], sys.argv[2:]
data = json.load(open(chunks_path))
chunks = data["chunks"]
questions = [json.loads(line) for line in open(f"{D}/questions.jsonl")]
tag = os.path.splitext(os.path.basename(chunks_path))[0]
# The ceiling: a question whose snippet straddles a chunk boundary (or was lost by the extractor) cannot
# be hit by any method, so recall is read against this.
by_doc = {}
for c in chunks:
    by_doc.setdefault(c["doc"], []).append(c["text"])
reachable = sum(any(evallib.is_hit(t, q["snippet"]) for t in by_doc.get(q["doc"], [])) for q in questions)
CEILING = round(reachable / len(questions), 3)


def emit(method, ranked, lat_ms=None, extra=None):
    m = evallib.metrics(ranked, questions, chunks)
    row = {"chunks": tag, "n_chunks": len(chunks), "ceiling": CEILING, "method": method, **m}
    if lat_ms:
        row["search_ms_p50"] = round(float(np.percentile(lat_ms, 50)), 2)
        row["search_ms_p95"] = round(float(np.percentile(lat_ms, 95)), 2)
    row.update(extra or {})
    print(json.dumps(row), flush=True)
    return row


bm = evallib.BM25(chunks)
bm_ranked, bm_lat = [], []
for q in questions:
    t = time.perf_counter()
    bm_ranked.append(bm.search(q["question"]))
    bm_lat.append((time.perf_counter() - t) * 1000)
emit("bm25", bm_ranked, bm_lat)

for p in prefixes:
    info = json.load(open(f"{p}.json"))
    name = info["model"].split("/")[-1]
    dense = evallib.Dense(np.load(f"{p}.docs.npy"))
    qv = np.load(f"{p}.queries.npy")
    d_ranked, d_lat = [], []
    for v in qv:
        t = time.perf_counter()
        d_ranked.append(dense.search(v))
        d_lat.append((time.perf_counter() - t) * 1000)
    extra = {"embed_chunks_per_second": round(info["chunks_per_second"], 1),
             "embed_seconds": round(info["embed_seconds"], 1), "query_embed_ms_p50": round(info["query_ms_p50"], 1),
             "query_embed_ms_p95": round(info["query_ms_p95"], 1), "model_rss_mb": round(info["maxrss_mb"])}
    emit(f"dense:{name}", d_ranked, d_lat, extra)
    h_ranked = [evallib.rrf(b, d) for b, d in zip(bm_ranked, d_ranked)]
    emit(f"hybrid:{name}", h_ranked, None, extra)
