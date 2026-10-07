"""Embed a chunk set and the questions with one CPU model: embed.py <model> <chunks.json> <questions.jsonl> <out-prefix>.
Writes <out>.docs.npy, <out>.queries.npy and <out>.json (timings, peak RSS). CPU only: onnxruntime's CPU
provider is the only one installed, and CUDA is hidden besides."""
import json
import os
import resource
import sys
import time

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
from fastembed import TextEmbedding  # noqa: E402

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

model, chunks_path, qpath, out = sys.argv[1:5]
THREADS = 4
PREFIX = {  # (document prefix, query prefix), as each model card prescribes
    "nomic-ai/nomic-embed-text-v1.5": ("search_document: ", "search_query: "),
    "nomic-ai/nomic-embed-text-v1.5-Q": ("search_document: ", "search_query: "),
    "BAAI/bge-small-en-v1.5": ("", "Represent this sentence for searching relevant passages: "),
    "snowflake/snowflake-arctic-embed-s": ("", "Represent this sentence for searching relevant passages: "),
    "snowflake/snowflake-arctic-embed-xs": ("", "Represent this sentence for searching relevant passages: "),
    "minishlab/potion-retrieval-32M": ("", ""),
}
dp, qp = PREFIX[model]
data = json.load(open(chunks_path))
chunks = data["chunks"]
questions = [json.loads(line) for line in open(qpath)]
t_load = time.perf_counter()
emb = TextEmbedding(model, threads=THREADS, cache_dir=f"{D}/models", providers=["CPUExecutionProvider"])
t_load = time.perf_counter() - t_load
texts = [dp + evallib.embed_text(c, c["title"]) for c in chunks]
t0 = time.perf_counter()
docs = np.stack(list(emb.embed(texts, batch_size=16))).astype(np.float32)
t_docs = time.perf_counter() - t0
lat = []
qv = []
for q in questions:
    t = time.perf_counter()
    qv.append(next(iter(emb.embed([qp + q["question"]]))))
    lat.append((time.perf_counter() - t) * 1000)
np.save(f"{out}.docs.npy", docs)
np.save(f"{out}.queries.npy", np.stack(qv).astype(np.float32))
info = {"model": model, "threads": THREADS, "chunks": len(chunks), "pdf_pages": data["pdf_pages"],
        "load_seconds": t_load, "embed_seconds": t_docs, "chunks_per_second": len(chunks) / t_docs,
        "query_ms_p50": float(np.percentile(lat, 50)), "query_ms_p95": float(np.percentile(lat, 95)),
        "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, "dim": int(docs.shape[1]),
        "loadavg_at_end": open("/proc/loadavg").read().split()[:3]}
json.dump(info, open(f"{out}.json", "w"), indent=1)
print(json.dumps(info))
