"""Does each PDF text pass give Arabic in logical order? rtl_check.py <pdf> [page] (debugging)."""
import os
import sys

import pypdfium2 as pdfium

D = os.path.dirname(os.path.abspath(__file__))
src = open(f"{D}/worker.py").read().split("engine, path = sys.argv")[0]
ns = {}
exec(compile(src, "worker", "exec"), ns)
doc = pdfium.PdfDocument(sys.argv[1])
pg = doc[int(sys.argv[2]) - 1 if len(sys.argv) > 2 else 0]
print("PLAIN  :", pg.get_textpage().get_text_bounded()[:400].replace("\n", " / "))
print("ORDERED:", ns["ordered_page"](pg)[:400].replace("\n", " / "))
try:
    import pymupdf
    print("PYMUPDF:", pymupdf.open(sys.argv[1])[0].get_text()[:400].replace("\n", " / "))
except ImportError:
    pass
