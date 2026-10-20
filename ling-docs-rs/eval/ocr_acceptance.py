"""OCR acceptance (spec §12, §20): ling-docs's own OCR on the 39 scanned fixtures, against Phase 0's route.

    ocr_acceptance.py <ling-docs wrapper> <out.json>

The wrapper runs the built `ling-docs` with MIGHTLING_DOCS_LIB_DIR and MIGHTLING_DOCS_OCR_DIR set (in a
capped scope, no network). `ling-docs ocr-file` reads each fixture twice: at the fixture's own resolution
(as ocr_bench.py rendered it, so the two pipelines see the same pixels) and as the index renders a page
(the resolution of its image). Word recall is ocr_bench.py's (the source text layer's words, characters
for Chinese, Japanese and Korean). The reference is the route replayed by ocr_report.py from
results/ocr-osd.json, ocr-v6-small.json and ocr-v5-all.json, measured on the same fixtures by
run_ocr.sh: Tesseract OSD, then PP-OCRv6 small for Latin and CJK, the PP-OCRv5 script model otherwise.
"""

import collections
import json
import re
import statistics
import subprocess
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
OSD_TO = {
    "Latin": "Latin",
    "Cyrillic": "Cyrillic",
    "Han": "Han",
    "HanS": "Han",
    "HanT": "Han",
    "Japanese": "Han",
    "Hangul": "Hangul",
    "Korean": "Hangul",
    "Arabic": "Arabic",
}
SCRIPT_GROUP = {
    "Latin": "Latin",
    "Cyrillic": "Cyrillic",
    "Han": "CJK",
    "Japanese": "CJK",
    "Hangul": "Hangul",
    "Arabic": "Arabic",
}
SCRIPT_OF = {
    "eng": "Latin",
    "deu": "Latin",
    "swe": "Latin",
    "fra": "Latin",
    "rus": "Cyrillic",
    "ukr": "Cyrillic",
    "chi_sim": "Han",
    "jpn": "Japanese",
    "kor": "Hangul",
    "ara": "Arabic",
}
CHAR_LANGS = ("chi_sim", "jpn", "kor")
V6_ROUTE = ("Latin", "Han")


def norm(s: str) -> str:
    """NFKC, lower case, whitespace collapsed (ocr_bench.py)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s).lower()).strip()


def is_cjk(c: str) -> bool:
    """Chinese, Japanese and Korean characters (ocr_bench.py)."""
    return "一" <= c <= "鿿" or "぀" <= c <= "ヿ" or "가" <= c <= "힯"


def tokens(s: str, chars: bool) -> list:
    """Words of two letters or more, or CJK characters (ocr_bench.py)."""
    s = norm(s)
    if chars:
        return [c for c in s if is_cjk(c)]
    return [w for w in re.findall(r"[^\W_]+", s) if len(w) > 1]


def recall(fx: dict, text: str) -> float:
    """Word recall against the fixture's source text layer."""
    chars = fx["tess_lang"] in CHAR_LANGS
    want, got = (
        collections.Counter(tokens(fx["text"], chars)),
        collections.Counter(tokens(text, chars)),
    )
    return round(sum((want & got).values()) / max(1, sum(want.values())), 3)


def ling(wrapper: str, path: Path, dpi: float | None) -> dict:
    """One fixture through `ling-docs ocr-file` (its first page)."""
    args = ["bash", wrapper, "ocr-file", str(path)] + (["--dpi", str(dpi)] if dpi else [])
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return json.loads(out.splitlines()[0])


def reference(fid: str, results: dict) -> dict | None:
    """Phase 0's route replayed for one fixture, when its result files are there."""
    if not all(results.values()):
        return None
    detected = results["osd"]["pages"][fid]["detected_script"]
    route = OSD_TO.get(detected, "Latin")
    osd_s = results["osd"]["pages"][fid]["seconds"]
    if route in V6_ROUTE:
        page = results["v6-small"]["pages"][fid]
        return {
            "recall": page["word_recall"],
            "seconds": page["seconds"] + osd_s,
            "script": detected,
        }
    model = results["v5-all"]["pages"][fid]["models"][route]
    return {"recall": model["word_recall"], "seconds": model["seconds"] + osd_s, "script": detected}


def summary(rows: list, key: str) -> dict:
    """Recall by script and kind, and over scans and photos of every script."""
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["group"], r["kind"])].append(r[key])
    table = {f"{g} {k}": round(statistics.mean(v), 3) for (g, k), v in sorted(by.items())}
    readable = [r[key] for r in rows if r["kind"] != "poor"]
    return {"by_script_kind": table, "scan_photo_all_scripts": round(statistics.mean(readable), 3)}


def main() -> None:
    """Reads every fixture both ways and writes the comparison."""
    wrapper, out = sys.argv[1], sys.argv[2]
    truth = json.loads((HERE / "ocr" / "truth.json").read_text())
    results = dict.fromkeys(("osd", "v6-small", "v5-all"))
    for name in results:
        path = HERE / "results" / f"ocr-{name}.json"
        if path.exists():
            results[name] = json.loads(path.read_text())
    rows = []
    for fid, fx in truth.items():
        path = HERE / fx["file"]
        native = ling(wrapper, path, fx["dpi"] if path.suffix == ".pdf" else None)
        indexed = native if path.suffix != ".pdf" else ling(wrapper, path, None)
        ref = reference(fid, results)
        row = {
            "fixture": fid,
            "group": SCRIPT_GROUP[SCRIPT_OF[fx["tess_lang"]]],
            "kind": fx["kind"],
            "ling_docs": recall(fx, native["text"]),
            "ling_docs_as_indexed": recall(fx, indexed["text"]),
            "seconds": round(native["seconds"], 2),
            "route": native["route"],
            "tesseract_script": native["tesseract_script"],
            "confidence": round(native["confidence"], 3),
        }
        if ref:
            row.update(
                reference=ref["recall"],
                reference_seconds=round(ref["seconds"], 2),
                reference_script=ref["script"],
            )
        rows.append(row)
        sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    result = {
        "fixtures": len(rows),
        "ling_docs": summary(rows, "ling_docs"),
        "ling_docs_as_indexed": summary(rows, "ling_docs_as_indexed"),
        "seconds_per_page_mean": round(statistics.mean(r["seconds"] for r in rows), 2),
        "pages": rows,
    }
    if all("reference" in r for r in rows):
        result["reference"] = summary(rows, "reference")
        result["reference_seconds_per_page_mean"] = round(
            statistics.mean(r["reference_seconds"] for r in rows), 2
        )
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    keys = ("ling_docs", "ling_docs_as_indexed", "reference")
    sys.stdout.write(json.dumps({k: result[k] for k in keys if k in result}, indent=1) + "\n")


if __name__ == "__main__":
    main()
