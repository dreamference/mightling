"""Recompute ocr/truth.json's text from the source pages with the current ordered pass, leaving the
degraded images alone (they carry unseeded noise): refresh_truth.py. Run after the right-to-left fix,
which turned the Arabic ground truth from visual into logical order."""
import json
import os

import pypdfium2 as pdfium

D = os.path.dirname(os.path.abspath(__file__))
ns = {}
exec(compile(open(f"{D}/worker.py").read().split("engine, path = sys.argv")[0], "worker", "exec"), ns)
truth = json.load(open(f"{D}/ocr/truth.json"))
changed = 0
for fid, fx in truth.items():
    text = ns["ordered_page"](pdfium.PdfDocument(f"{D}/{fx['source']}")[fx["page"] - 1])
    changed += text != fx["text"]
    fx["text"] = text
json.dump(truth, open(f"{D}/ocr/truth.json", "w"), ensure_ascii=False, indent=1)
print(changed, "of", len(truth), "truth texts changed")
