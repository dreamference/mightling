"""Extract every corpus document and chunk it: build_chunks.py <pdf-engine> <target-tokens> [<tokenizer>]
-> chunks/<engine>-<target>.json (the XLM-R tokenizer of the multilingual candidates, the default) or
chunks/<engine>-<target>-<tokenizer>.json. PDF pages come from results/pdf-<engine>.json (the sandboxed
extractor run); other formats from evallib."""
import json
import os
import re
import sys
import time

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

engine, target = sys.argv[1], int(sys.argv[2])
tokenizer = sys.argv[3] if len(sys.argv) > 3 else "intfloat/multilingual-e5-small"
suffix = "" if len(sys.argv) <= 3 else "-" + tokenizer.split("/")[-1]
root = f"{D}/corpus"
raw = json.load(open(f"{D}/results/pdf-{engine}.json"))
pdf_pages = {k[len("corpus/"):]: v.get("pages", []) for k, v in raw.items() if k.startswith("corpus/") and v["ok"]}
docs = sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fs in os.walk(root) for f in fs
              if not dp.endswith("_docx_src") and not f.startswith((".", "manifest_fetched", "CACHEDIR")))
count = evallib.Counter(tokenizer)
t0 = time.perf_counter()
chunks, pages_total = [], 0
for rel in docs:
    units = evallib.extract(root, rel, pdf_pages)
    pages_total += len(pdf_pages.get(rel, [])) if rel.endswith(".pdf") else 0
    title = os.path.splitext(os.path.basename(rel))[0].replace("_", " ")
    if rel.endswith(".html"):  # the page's own title, in its own language, not the file name
        m = re.search(r"<title>([^<]+)</title>", open(os.path.join(root, rel), encoding="utf-8").read(4096))
        title = m.group(1).strip() if m else title
    for c in evallib.chunk(rel, units, count, target=target):
        c["title"] = title
        chunks.append(c)
os.makedirs(f"{D}/chunks", exist_ok=True)
json.dump({"engine": engine, "target": target, "tokenizer": tokenizer, "docs": len(docs), "pdf_pages": pages_total,
           "chunks": chunks, "seconds": time.perf_counter() - t0},
          open(f"{D}/chunks/{engine}-{target}{suffix}.json", "w"))
print(engine, target, tokenizer, "docs", len(docs), "chunks", len(chunks), "pdf pages", pages_total,
      f"{time.perf_counter() - t0:.1f}s")
