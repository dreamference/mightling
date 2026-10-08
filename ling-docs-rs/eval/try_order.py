"""Run worker.ordered_page on one page and print it: try_order.py file page [chars] (no sandbox; debugging)."""
import importlib.util
import os
import sys

import pypdfium2 as pdfium

D = os.path.dirname(os.path.abspath(__file__))
src = open(f"{D}/worker.py").read().split("engine, path = sys.argv")[0]
ns = {}
exec(compile(src, "worker", "exec"), ns)
doc = pdfium.PdfDocument(sys.argv[1])
n = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
print(ns["ordered_page"](doc[int(sys.argv[2]) - 1])[:n])
