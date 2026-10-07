"""Check every question's snippet occurs in its document (PDFs through PDFium, the spec's engine) and
fill in the locator: validate.py corpus questions.jsonl."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib  # noqa: E402

root, qpath = sys.argv[1], sys.argv[2]
qs = [json.loads(line) for line in open(qpath)]
cache, bad = {}, 0
for q in qs:
    if q["doc"] not in cache:
        if q["doc"].endswith(".pdf"):
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(os.path.join(root, q["doc"]))
            pages = [doc[i].get_textpage().get_text_bounded() for i in range(len(doc))]
            cache[q["doc"]] = evallib.units_pdf(pages)
        else:
            cache[q["doc"]] = evallib.extract(root, q["doc"])
    units = cache[q["doc"]]
    locs = [u["loc"] for u in units if evallib.is_hit(u["text"], q["snippet"])]
    if not locs:
        bad += 1
        print("MISSING", q["id"], q["doc"], q["snippet"][:70])
        q["loc"] = None
    else:
        q["loc"] = locs[0]
with open(qpath, "w") as f:
    for q in qs:
        f.write(json.dumps(q, ensure_ascii=False) + "\n")
print(len(qs) - bad, "/", len(qs), "found")
