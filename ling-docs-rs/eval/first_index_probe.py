"""The extraction half of a first index over real folders, sandboxed exactly as the bench (bwrap, no
network, 1 GB cap, 30 s): filter by extension, sniff, dedupe by SHA-256, extract every PDF with the
ordered PDFium pass and read the text formats. Prints aggregates only (no file name or text leaves this
script). first_index_probe.py <folder> […] -> results/first-index-<label>.json"""
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402
from filter_bench import SUPPORTED, sniff_ok  # noqa: E402

label, roots = sys.argv[1], sys.argv[2:]
src = open(f"{D}/bench_pdf.py").read().split("for engine in ENGINES:")[0]
argv, sys.argv = sys.argv, [sys.argv[0], "none"]
ns = {"__file__": f"{D}/bench_pdf.py"}
exec(compile(src, "bench_pdf", "exec"), ns)
sys.argv = argv

t0 = time.perf_counter()
files, cands, seen = 0, [], set()
skipped_bytes = dup = bad = 0
for root in roots:
    for dp, dns, fns in os.walk(os.path.expanduser(root)):
        dns[:] = [d for d in dns if not d.startswith((".", "_"))]
        for f in fns:
            if f.startswith("."):
                continue
            p = os.path.join(dp, f)
            files += 1
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if os.path.splitext(f)[1].lower() not in SUPPORTED or not os.path.isfile(p) or os.path.islink(p):
                skipped_bytes += st.st_size
                continue
            if st.st_size > 50 << 20:  # max_file_mb default (§5.1)
                skipped_bytes += st.st_size
                continue
            with open(p, "rb") as fh:
                head = fh.read(8192)
            if not sniff_ok(p, head):
                bad += 1
                continue
            h = hashlib.sha256(open(p, "rb").read()).digest()
            if h in seen:
                dup += 1
                continue
            seen.add(h)
            cands.append(p)
t_filter = time.perf_counter() - t0
pages = chars = failed = 0
pdf_rss, pdf_secs = [], []
t1 = time.perf_counter()
for p in cands:
    if p.lower().endswith(".pdf"):
        r = ns["run"]("pdfium-ordered", p)
        if not r["ok"]:
            failed += 1
            continue
        pages += len(r["pages"])
        chars += sum(len(x) for x in r["pages"])
        pdf_rss.append(r["maxrss_kb"] / 1024)
        pdf_secs.append(r["seconds"])
    else:
        try:
            units = evallib.extract(os.path.dirname(p), os.path.basename(p))
            chars += sum(len(u["text"]) for u in units)
        except Exception:  # noqa: BLE001
            failed += 1
t_extract = time.perf_counter() - t1
# Chunks: the corpus averaged this many characters per 512-token chunk.
corpus = json.load(open(f"{D}/chunks/pdfium-ordered-512.json"))["chunks"]
cpc = sum(len(c["text"]) for c in corpus) / len(corpus)
out = {"label": label, "files_seen": files, "indexable_unique": len(cands), "duplicates": dup,
       "extension_lies": bad, "skipped_unopened_gib": round(skipped_bytes / 2**30, 2),
       "filter_dedupe_seconds": round(t_filter, 2), "extract_seconds": round(t_extract, 1),
       "pdf_files": len(pdf_rss), "pdf_pages": pages, "failed": failed, "text_mchars": round(chars / 1e6, 2),
       "est_chunks_512": round(chars / cpc), "pdf_peak_rss_mb_median": round(statistics.median(pdf_rss)) if pdf_rss else None,
       "pdf_peak_rss_mb_max": round(max(pdf_rss)) if pdf_rss else None}
json.dump(out, open(f"{D}/results/first-index-{label}.json", "w"), indent=1)
print(json.dumps(out, indent=1))
