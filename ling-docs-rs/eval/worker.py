"""Extract one PDF with one engine and print JSON: {ok, pages|error, seconds, maxrss_kb}.
Run inside bwrap (no network) and a memory-capped scope by bench.py."""
import json
import os
import resource
import subprocess
import sys
import time

engine, path = sys.argv[1], sys.argv[2]
RUST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rustpdf/target/release/rustpdf")
t0 = time.perf_counter()
out = {"engine": engine}
try:
    if engine == "pdfium":
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(path)
        pages = []
        for i in range(len(doc)):
            tp = doc[i].get_textpage()
            pages.append(tp.get_text_bounded())
        out.update(ok=True, pages=pages)
    elif engine == "pymupdf":
        import pymupdf
        doc = pymupdf.open(path)
        out.update(ok=True, pages=[p.get_text("text", sort=False) for p in doc])
    elif engine == "pymupdf-sort":
        import pymupdf
        doc = pymupdf.open(path)
        out.update(ok=True, pages=[p.get_text("text", sort=True) for p in doc])
    elif engine == "pdfminer":
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer
        pages = []
        for layout in extract_pages(path):
            pages.append("".join(el.get_text() for el in layout if isinstance(el, LTTextContainer)))
        out.update(ok=True, pages=pages)
    elif engine == "pypdf":
        from pypdf import PdfReader
        out.update(ok=True, pages=[(p.extract_text() or "") for p in PdfReader(path).pages])
    elif engine == "pdftotext":
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="replace")[:300])
        out.update(ok=True, pages=r.stdout.decode("utf-8", errors="replace").split("\f")[:-1] or [""])
    elif engine in ("pdf-extract", "pdf_oxide"):
        r = subprocess.run([RUST, engine, path], capture_output=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"exit {r.returncode}: " + r.stderr.decode(errors="replace")[-300:])
        res = json.loads(r.stdout)
        if not res["ok"]:
            raise RuntimeError(res["error"])
        out.update(ok=True, pages=res["pages"])
    else:
        raise ValueError(engine)
except Exception as exc:  # noqa: BLE001
    out.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")
out["seconds"] = time.perf_counter() - t0
own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
out["maxrss_kb"] = max(own, kids)
print(json.dumps(out))
