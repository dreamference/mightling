"""Extract every corpus document and chunk it: build_chunks.py <pdf-engine> <target-tokens> -> chunks/<engine>-<target>.json.
PDF pages come from results/pdf-<engine>.json (the sandboxed extractor run); other formats from evallib."""
import json
import os
import sys
import time

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

engine, target = sys.argv[1], int(sys.argv[2])
root = f"{D}/corpus"
raw = json.load(open(f"{D}/results/pdf-{engine}.json"))
pdf_pages = {k[len("corpus/"):]: v.get("pages", []) for k, v in raw.items() if k.startswith("corpus/") and v["ok"]}
docs = sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fs in os.walk(root) for f in fs
              if not dp.endswith("_docx_src") and f != "manifest_fetched.json")
count = evallib.Counter()
t0 = time.perf_counter()
chunks, pages_total = [], 0
for rel in docs:
    units = evallib.extract(root, rel, pdf_pages)
    pages_total += len(pdf_pages.get(rel, [])) if rel.endswith(".pdf") else 0
    title = os.path.splitext(os.path.basename(rel))[0].replace("_", " ")
    for c in evallib.chunk(rel, units, count, target=target):
        c["title"] = title
        chunks.append(c)
os.makedirs(f"{D}/chunks", exist_ok=True)
json.dump({"engine": engine, "target": target, "docs": len(docs), "pdf_pages": pages_total, "chunks": chunks,
           "seconds": time.perf_counter() - t0}, open(f"{D}/chunks/{engine}-{target}.json", "w"))
print(engine, target, "docs", len(docs), "chunks", len(chunks), "pdf pages", pages_total,
      f"{time.perf_counter() - t0:.1f}s")
