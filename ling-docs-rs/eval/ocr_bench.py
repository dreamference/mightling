"""OCR on the scanned fixtures: ocr_bench.py <engine> -> results/ocr-<engine>.json. Engines:
  v6-tiny, v6-small, v6-medium   RapidOCR PP-OCRv6, one multilingual model for every script
  v5-oracle                      RapidOCR PP-OCRv5 mobile with the per-script model of the page's real script
  v5-all                         every PP-OCRv5 recognition model on every page, each timed alone (the
                                 per-page selection rules are replayed from these by ocr_report.py)
  tesseract                      Tesseract 5.3.4, tessdata_fast, the page's language given
  osd                            Tesseract's script detection alone (--psm 0), for the selection question
Run by run_ocr.sh in a memory-capped scope with no network, four X925 cores, four threads.
Per page: seconds, word recall against the source's text layer (multiset; characters for Chinese, Japanese
and Korean, whose OCR output drops or moves the spaces), character error rate (order-sensitive: a two-column page read in another order scores badly
at full recall), detected script, mean confidence, every recognised word with its confidence.
(A v5-detect engine that read the script from a v6-tiny pass was dropped: v6 reads Cyrillic, Hangul and
Arabic pages as Latin garbage, so it detected Latin for exactly the pages that needed another model.)"""
import collections
import json
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
import unicodedata

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402
from PIL import Image  # noqa: E402
from rapidfuzz.distance import Levenshtein  # noqa: E402

D = os.path.dirname(os.path.abspath(__file__))
engine = sys.argv[1]
truth = json.load(open(f"{D}/ocr/truth.json"))
TESS = f"{D}/tess/root/usr/bin/tesseract"
TESS_ENV = dict(os.environ, LD_LIBRARY_PATH=f"{D}/tess/root/usr/lib/aarch64-linux-gnu", OMP_THREAD_LIMIT="4",
                TESSDATA_PREFIX=f"{D}/tess/tessdata")
SCRIPT_OF = {"eng": "Latin", "deu": "Latin", "swe": "Latin", "fra": "Latin", "rus": "Cyrillic", "ukr": "Cyrillic",
             "chi_sim": "Han", "jpn": "Japanese", "kor": "Hangul", "ara": "Arabic"}


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s).lower()).strip()


def is_cjk(c):
    return "一" <= c <= "鿿" or "぀" <= c <= "ヿ" or "가" <= c <= "힯"


def tokens(s, chars):
    s = norm(s)
    if chars:
        return [c for c in s if is_cjk(c)]
    return [w for w in re.findall(r"[^\W_]+", s) if len(w) > 1]


def script_of_text(s):
    n = collections.Counter()
    for c in s:
        if not c.isalpha():
            continue
        o = ord(c)
        n["Hangul" if 0xAC00 <= o <= 0xD7AF else "Kana" if 0x3040 <= o <= 0x30FF else
          "Han" if 0x4E00 <= o <= 0x9FFF else "Arabic" if 0x0600 <= o <= 0x06FF else
          "Cyrillic" if 0x0400 <= o <= 0x04FF else "Latin" if o < 0x0250 else "Other"] += 1
    total = sum(n.values()) or 1
    if n["Hangul"] / total > 0.15:
        return "Hangul"
    if n["Kana"] / total > 0.05:
        return "Japanese"
    if n["Han"] / total > 0.15:
        return "Han"
    if n["Arabic"] / total > 0.15:
        return "Arabic"
    if n["Cyrillic"] / total > 0.15:
        return "Cyrillic"
    return "Latin"


def load(fx):
    p = f"{D}/{fx['file']}"
    if p.endswith(".pdf"):
        page = pdfium.PdfDocument(p)[0]
        return page.render(scale=fx["dpi"] / 72, grayscale=True).to_pil().convert("RGB")
    return Image.open(p).convert("RGB")


Q = {"Global.return_word_box": True, "Global.log_level": "warning",
     "EngineConfig.onnxruntime.intra_op_num_threads": 4, "EngineConfig.onnxruntime.inter_op_num_threads": 1}
engines = {}


def rapid(kind):
    from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR
    if kind not in engines:
        if kind.startswith("v6-"):
            mt = {"v6-tiny": ModelType.TINY, "v6-small": ModelType.SMALL, "v6-medium": ModelType.MEDIUM}[kind]
            p = {**Q, "Det.model_type": mt, "Rec.model_type": mt}
        else:
            lang = {"Latin": LangRec.LATIN, "Cyrillic": LangRec.ESLAV, "Han": LangRec.CH, "Japanese": LangRec.CH,
                    "Hangul": LangRec.KOREAN, "Arabic": LangRec.ARABIC}[kind]
            p = {**Q, "Det.ocr_version": OCRVersion.PPOCRV5, "Det.lang_type": LangDet.CH,
                 "Det.model_type": ModelType.MOBILE, "Rec.ocr_version": OCRVersion.PPOCRV5,
                 "Rec.lang_type": lang, "Rec.model_type": ModelType.MOBILE}
        engines[kind] = RapidOCR(params=p)
        engines[kind](np.full((64, 64, 3), 255, np.uint8))  # load before timing
    return engines[kind]


def run_rapid(kind, img):
    r = rapid(kind)(np.asarray(img))
    words = [(w, float(s)) for line in (r.word_results or []) for w, s, _ in line]
    return "\n".join(r.txts or []), words


def run_tess(img, lang, psm="3"):
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        img.save(tmp.name)
        if psm == "0":
            out = subprocess.run([TESS, tmp.name, "-", "--psm", "0"], capture_output=True, text=True, env=TESS_ENV)
            m = re.search(r"Script: (\w+)", out.stdout)
            return (m.group(1) if m else "none"), []
        out = subprocess.run([TESS, tmp.name, "-", "-l", lang, "--psm", psm, "tsv"],
                             capture_output=True, text=True, env=TESS_ENV).stdout
    cur, lines, words = None, [], []
    for row in (ln.split("\t") for ln in out.splitlines()[1:]):
        if len(row) == 12 and row[11].strip():
            key = tuple(row[1:5])
            if key != cur:
                lines.append([]); cur = key
            lines[-1].append(row[11])
            words.append((row[11], float(row[10]) / 100))
    return "\n".join(" ".join(ln) for ln in lines), words


# Warm the engines this run will use, so the first page's time is not a model load.
V5_ALL = ["Latin", "Cyrillic", "Han", "Hangul", "Arabic"]  # Japanese shares the Chinese model in v5
if engine.startswith("v6-"):
    rapid(engine)
elif engine == "v5-all":
    for k in V5_ALL:
        rapid(k)


def score(fx, text, words):
    chars = fx["tess_lang"] in ("chi_sim", "jpn", "kor")
    want, got = collections.Counter(tokens(fx["text"], chars)), collections.Counter(tokens(text, chars))
    vocab = set(want)
    scored = [(s, all(x in vocab for x in tw)) for w, s in words if (tw := tokens(w, chars))]
    return dict(word_recall=round(sum((want & got).values()) / max(1, sum(want.values())), 3),
                cer=round(Levenshtein.normalized_distance(norm(fx["text"]), norm(text)), 3), words=scored,
                mean_conf=round(float(np.mean([s for _, s in words])), 3) if words else 0.0, n_words=len(words))


pages = {}
for fid, fx in truth.items():
    img = load(fx)
    real = SCRIPT_OF[fx["tess_lang"]]
    rec = {"kind": fx["kind"], "lang": fx["tess_lang"], "script": real, "pixels": img.width * img.height}
    if engine == "v5-all":
        # Every PP-OCRv5 recognition model on every page, each timed alone: the report replays the
        # selection rules (oracle, best confidence, v6 then fallback below a threshold) from these.
        rec["models"] = {}
        for k in V5_ALL:
            t = time.perf_counter()
            text, words = run_rapid(k, img)
            secs = time.perf_counter() - t
            s = score(fx, text, words)
            s.pop("words")
            rec["models"][k] = {"seconds": round(secs, 2), **s}
        pages[fid] = rec
        print(engine, fid, {k: (v["word_recall"], v["mean_conf"]) for k, v in rec["models"].items()}, flush=True)
        continue
    t = time.perf_counter()
    detected = None
    if engine.startswith("v6-"):
        text, words = run_rapid(engine, img)
        detected = script_of_text(text)
    elif engine == "v5-oracle":
        text, words = run_rapid(real if real != "Japanese" else "Han", img)
    elif engine == "tesseract":
        text, words = run_tess(img, fx["tess_lang"])
    elif engine == "osd":
        detected, words = run_tess(img, None, psm="0")
        text = ""
    secs = time.perf_counter() - t
    rec.update(seconds=round(secs, 2), detected_script=detected)
    if engine != "osd":
        rec.update(score(fx, text, words))
    pages[fid] = rec
    print(engine, fid, f"{secs:.1f}s", rec.get("word_recall"), rec.get("mean_conf"), detected, flush=True)

ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
json.dump({"engine": engine, "peak_rss_mb": round(max(ru, kids)), "python_rss_mb": round(ru),
           "tesseract_rss_mb": round(kids), "pages": pages}, open(f"{D}/results/ocr-{engine}.json", "w"))
