"""Right-to-left text from PDFs: share of the Arabic words of each source PDF that each pass yields,
against PyMuPDF (which applies the bidi algorithm) as the reference; the word is compared whole, so a
reversed word counts as missing. rtl_score.py <pdf> … -> results/rtl-arabic.json"""
import collections
import json
import os
import re
import sys
import unicodedata

import pymupdf
import pypdfium2 as pdfium

D = os.path.dirname(os.path.abspath(__file__))
ns = {}
exec(compile(open(f"{D}/worker.py").read().split("engine, path = sys.argv")[0], "worker", "exec"), ns)
old = {}
if os.path.exists(f"{D}/worker.py.pre-rtl"):  # the ordered pass before the bidi step, for the record
    exec(compile(open(f"{D}/worker.py.pre-rtl").read().split("engine, path = sys.argv")[0], "old", "exec"), old)
ARABIC = re.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]{2,}")


def words(t):
    return collections.Counter(ARABIC.findall(unicodedata.normalize("NFKC", t)))


out = {}
for path in sys.argv[1:]:
    ref = words("\n".join(p.get_text() for p in pymupdf.open(path)))
    doc = pdfium.PdfDocument(path)
    plain = words("\n".join(doc[i].get_textpage().get_text_bounded() for i in range(len(doc))))
    ordered = words("\n".join(ns["ordered_page"](doc[i]) for i in range(len(doc))))
    total = sum(ref.values())
    out[os.path.basename(path)] = {"reference_words": total,
                                   "pdfium_plain": round(sum((ref & plain).values()) / total, 3),
                                   "pdfium_ordered_with_bidi": round(sum((ref & ordered).values()) / total, 3)}
    if old:
        before = words("\n".join(old["ordered_page"](doc[i]) for i in range(len(doc))))
        out[os.path.basename(path)]["pdfium_ordered_before_bidi"] = round(sum((ref & before).values()) / total, 3)
print(json.dumps(out, indent=1))
json.dump(out, open(f"{D}/results/rtl-arabic.json", "w"), indent=1)
