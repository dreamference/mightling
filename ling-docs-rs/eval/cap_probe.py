"""At what memory cap does PDFium get through the two files it was OOM-killed on at 1 GB? Writes results/pdfium-caps.json."""
import json
import os
import sys
import time

D = os.path.dirname(os.path.abspath(__file__))
src = open(f"{D}/bench_pdf.py").read().split("for engine in ENGINES:")[0]
sys.argv = [sys.argv[0], "none"]
ns = {"__file__": f"{D}/bench_pdf.py"}
exec(compile(src, "bench_pdf", "exec"), ns)
out = {}
for f in ("flate-bomb.pdf", "text-ops.pdf"):
    for cap in ("2G", "3G", "4G"):
        t = time.perf_counter()
        r = ns["run"]("pdfium", f"{D}/hostile/{f}", cap=cap)
        res = "ok" if r["ok"] else r["error"][:30]
        rss = round(r["maxrss_kb"] / 1024) if r.get("maxrss_kb") else None
        out[f"{f} @ {cap}"] = {"result": res, "seconds": round(time.perf_counter() - t, 1), "peak_rss_mb": rss}
        print(f, cap, out[f"{f} @ {cap}"], flush=True)
        if r["ok"]:
            break
json.dump(out, open(f"{D}/results/pdfium-caps.json", "w"), indent=1)
