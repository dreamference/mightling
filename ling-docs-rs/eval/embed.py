"""Embed a chunk set and the questions with one CPU model:
  embed.py <model> <chunks.json> <questions.jsonl> <out-prefix> [<probe-chunks>]
Writes <out>.docs.npy, <out>.queries.npy and <out>.json (timings, peak RSS). With <probe-chunks>, embeds
only that many chunks (a fixed sample spread over the set and its languages) and the questions, and
writes <out>.json alone: the throughput probe. CPU only: onnxruntime's CPU provider is the only one
installed, and CUDA is hidden besides."""
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
from candidates import CANDIDATES, register  # noqa: E402

model, chunks_path, qpath, out = sys.argv[1:5]
probe = int(sys.argv[5]) if len(sys.argv) > 5 else 0
THREADS = 4
dp, qp, licence, _ = CANDIDATES[model]
register(model)
data = json.load(open(chunks_path))
chunks = data["chunks"]
if probe:
    step = max(1, len(chunks) // probe)
    chunks = chunks[::step][:probe]
questions = [json.loads(line) for line in open(qpath)]
t_load = time.perf_counter()
# MODEL_DIR: a plain copy of the files, for exports whose external weights ONNX Runtime refuses to follow
# through the hub cache's symlinks (bge-m3). BATCH: smaller batches for the models that pass 4 GB at 16.
BATCH = int(os.environ.get("BATCH", "16"))
extra = {"specific_model_path": os.environ["MODEL_DIR"]} if os.environ.get("MODEL_DIR") else {}
emb = TextEmbedding(model, threads=THREADS, cache_dir=f"{D}/models", providers=["CPUExecutionProvider"], **extra)
t_load = time.perf_counter() - t_load
rss_loaded = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
texts = [dp + evallib.embed_text(c, c["title"]) for c in chunks]
r0 = resource.getrusage(resource.RUSAGE_SELF)
t0 = time.perf_counter()
docs = np.stack(list(emb.embed(texts, batch_size=BATCH))).astype(np.float32)
t_docs = time.perf_counter() - t0
r1 = resource.getrusage(resource.RUSAGE_SELF)
# CPU seconds are robust to other load on the pinned cores; wall seconds are what a user waits.
cpu_docs = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
lat = []
qv = []
for q in questions:
    t = time.perf_counter()
    qv.append(next(iter(emb.embed([qp + q["question"]]))))
    lat.append((time.perf_counter() - t) * 1000)
if not probe:
    np.save(f"{out}.docs.npy", docs)
    np.save(f"{out}.queries.npy", np.stack(qv).astype(np.float32))
info = {"model": model, "licence": licence, "threads": THREADS, "chunks": len(chunks), "probe": bool(probe),
        "chunk_tokens": data["target"], "batch": BATCH, "load_seconds": t_load, "embed_seconds": t_docs,
        "chunks_per_second": len(chunks) / t_docs, "embed_cpu_seconds": cpu_docs,
        "chunks_per_cpu_second": len(chunks) / cpu_docs, "cpu_share_of_4_cores": cpu_docs / t_docs / THREADS,
        "query_ms_p50": float(np.percentile(lat, 50)), "query_ms_p95": float(np.percentile(lat, 95)),
        "rss_after_load_mb": rss_loaded, "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "dim": int(docs.shape[1]), "loadavg_at_end": open("/proc/loadavg").read().split()[:3]}
json.dump(info, open(f"{out}.json", "w"), indent=1)
print(json.dumps(info))
