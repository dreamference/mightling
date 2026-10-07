"""Summarise the sandboxed PDF extractor runs: speed, memory, hostile-file outcomes and text quality
(question snippets found; 8-word shingle recall against the RFCs' own .txt). Writes results/pdf-summary.json."""
import json
import os
import re
import statistics
import sys

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

ENGINES = ["pdfium", "pymupdf", "pymupdf-sort", "pdftotext", "pdf_oxide", "pdf-extract", "pypdf", "pdfminer"]
LICENCE = {"pdfium": "Apache-2.0 / BSD-3 (pypdfium2; PDFium BSD-3); Rust: pdfium-render MIT OR Apache-2.0",
           "pymupdf": "AGPL-3.0 (or commercial)", "pymupdf-sort": "AGPL-3.0 (or commercial)",
           "pdftotext": "GPL-2.0/3.0 (Poppler), external process only", "pdf_oxide": "MIT OR Apache-2.0 (Rust)",
           "pdf-extract": "MIT (Rust)", "pypdf": "BSD-3-Clause", "pdfminer": "MIT"}
qs = [json.loads(line) for line in open(f"{D}/questions.jsonl")]
pdf_qs = [q for q in qs if q["doc"].endswith(".pdf")]
words = re.compile(r"[a-z0-9]+")


def shingles(text, n=8):
    w = words.findall(text.lower())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def strip_rfc_txt(t):
    # Drop the .txt page furniture (form feeds, running headers/footers) before comparing.
    lines = [ln for ln in t.splitlines() if not re.match(r"^(RFC \d+ .*\d{4}|.*\[Page \d+\])\s*$", ln)]
    return "\n".join(lines)


refs = {f"rfc{n}": shingles(strip_rfc_txt(open(f"{D}/reference/rfc{n}.txt").read())) for n in (9110, 9114, 9293)}


def classify(r):
    if r["ok"]:
        return "ok" if any(p.strip() for p in r["pages"]) else "ok, empty text (no error)"
    e = r["error"]
    # Verified by re-running with wall-clock timing and the user journal: -15 is the scope stopped after
    # an OOM kill at the 1 GB cap (1-3 s), -9 is the 30 s `timeout -s KILL`.
    if e.startswith("exit -15"):
        return "killed at memory cap"
    if e.startswith("exit -9") or e.startswith("killed") or e.startswith("timeout") or "exit 137" in e:
        return "timeout (30 s)"
    if "exit -6" in e or "exit 101" in e or "exit -11" in e or "RuntimeError: exit" in e:
        return "crash (process abort/panic)"
    if "Recursion" in e:
        return "error (recursion)"
    return "error (clean)"


summary = {}
for eng in ENGINES:
    p = f"{D}/results/pdf-{eng}.json"
    if not os.path.exists(p):
        continue
    res = json.load(open(p))
    corpus = {k: v for k, v in res.items() if k.startswith("corpus/")}
    hostile = {os.path.basename(k): classify(v) for k, v in res.items() if k.startswith("hostile/")}
    ok = [v for v in corpus.values() if v["ok"]]
    pages = sum(len(v["pages"]) for v in ok)
    secs = sum(v["seconds"] for v in ok)
    empty = sum(1 for v in ok for pg in v["pages"] if len(pg.strip()) < 20)
    found = 0
    for q in pdf_qs:
        r = corpus.get("corpus/" + q["doc"])
        if r and r["ok"] and any(evallib.is_hit(pg, q["snippet"]) for pg in r["pages"]):
            found += 1
    rfc = {}
    for name, ref in refs.items():
        r = corpus.get(f"corpus/pdf/{name}.pdf")
        if r and r["ok"]:
            got = shingles("\n".join(r["pages"]))
            rfc[name] = round(len(ref & got) / len(ref), 3)
    big = corpus.get("corpus/pdf/eia-monthly-energy-review-2025-09.pdf", {})
    summary[eng] = {
        "licence": LICENCE[eng],
        "corpus_ok": f"{len(ok)}/{len(corpus)}",
        "corpus_failures": {k: v["error"][:100] for k, v in corpus.items() if not v["ok"]},
        "pages": pages, "seconds": round(secs, 1), "pages_per_second": round(pages / secs, 1) if secs else None,
        "eia_298_pages_seconds": round(big["seconds"], 1) if big.get("ok") else None,
        "peak_rss_mb_median": round(statistics.median(v["maxrss_kb"] for v in ok) / 1024),
        "peak_rss_mb_max": round(max(v["maxrss_kb"] for v in ok) / 1024),
        "near_empty_pages": empty,
        "pdf_question_snippets_found": f"{found}/{len(pdf_qs)}",
        "rfc_8gram_recall": rfc,
        "hostile": hostile,
    }
json.dump(summary, open(f"{D}/results/pdf-summary.json", "w"), indent=1)
for eng, s in summary.items():
    print(f"\n== {eng}: ok {s['corpus_ok']}, {s['pages']} pages in {s['seconds']}s ({s['pages_per_second']} p/s), "
          f"EIA {s['eia_298_pages_seconds']}s, RSS median {s['peak_rss_mb_median']} MB max {s['peak_rss_mb_max']} MB, "
          f"empty pages {s['near_empty_pages']}, snippets {s['pdf_question_snippets_found']}, RFC {s['rfc_8gram_recall']}")
    if s["corpus_failures"]:
        print("   corpus failures:", s["corpus_failures"])
    print("   hostile:", s["hostile"])
