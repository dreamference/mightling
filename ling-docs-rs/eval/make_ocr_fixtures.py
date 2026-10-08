"""Scanned-document fixtures for the OCR measurement. Each source page has a known text layer (the
ground truth); it is rendered with PDFium and degraded three ways:
  scan   200 dpi greyscale, 0.6° skew, sensor noise, JPEG q60, wrapped as an image-only PDF
  photo  phone photo of paper: perspective, uneven light, slight blur, JPEG q80 (.jpg)
  poor   100 dpi, 2.5° skew, blur, heavy noise, JPEG q35 (image-only PDF): the case to hand on
Sources: English pages from the corpus (prose, two columns, a table, an RFC) and the Atlantic ling
article as Wikipedia renders it in German, Swedish, French, Russian, Ukrainian, Chinese, Japanese, Korean
and Arabic (CC BY-SA 4.0).
make_ocr_fixtures.py -> ocr/fixtures/*.{pdf,jpg} and ocr/truth.json"""
import hashlib
import io
import json
import os
import sys
import urllib.parse
import urllib.request

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
src = open(f"{D}/worker.py").read().split("engine, path = sys.argv")[0]
ns = {}
exec(compile(src, "worker", "exec"), ns)
ordered_page = ns["ordered_page"]
OUT = f"{D}/ocr"
os.makedirs(f"{OUT}/fixtures", exist_ok=True)
os.makedirs(f"{OUT}/sources", exist_ok=True)
UA = {"User-Agent": "mightling-eval/0.1 (dgxcoder@dreamference.ai)"}
rng = np.random.default_rng(7)

SOURCES = [  # (name, pdf path, 1-based page, language code for Tesseract)
    ("en-prose", f"{D}/corpus/pdf/nist-sp-800-63b.pdf", 24, "eng"),
    ("en-twocol", f"{D}/corpus/pdf/arxiv-2610.05783.pdf", 9, "eng"),
    ("en-table", f"{D}/corpus/pdf/bls-empsit-2025-09.pdf", 20, "eng"),
    ("en-rfc", f"{D}/corpus/pdf/rfc9110.pdf", 25, "eng"),
]
manifest = []
for lang, title, tess in [("de", "Papageitaucher", "deu"), ("sv", "Lunnefågel", "swe"),
                          ("fr", "Macareux moine", "fra"), ("ru", "Тупик (птица)", "rus"),
                          ("zh", "北极海鹦", "chi_sim"), ("ja", "ニシツノメドリ", "jpn"),
                          ("ko", "코뿔바다오리", "kor"), ("ar", "بفن أطلسي", "ara"), ("uk", "Іпатка атлантична", "ukr")]:
    path = f"{OUT}/sources/wikipedia-{lang}.pdf"
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/pdf/{urllib.parse.quote(title.replace(' ', '_'))}"
    if not os.path.exists(path):
        open(path, "wb").write(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read())
    data = open(path, "rb").read()
    manifest.append({"path": f"ocr/sources/wikipedia-{lang}.pdf", "url": url, "sha256": hashlib.sha256(data).hexdigest(),
                     "licence": "CC BY-SA 4.0 (Wikipedia contributors)"})
    SOURCES.append((f"{lang}-wiki", path, 2, tess))


def skew(img, deg):
    return img.rotate(deg, resample=Image.BICUBIC, expand=True, fillcolor=255)


def noise(img, sigma):
    a = np.asarray(img, dtype=np.float32) + rng.normal(0, sigma, (img.height, img.width))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def jpeg(img, q):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue()))


def perspective(img):
    w, h = img.size
    dx, dy = w * 0.06, h * 0.04
    # Map the output rectangle onto a trapezoid: the page photographed from slightly above and left.
    src_quad = [(dx, dy), (w - dx * 0.3, 0), (w, h), (0, h - dy)]
    coeffs = _coeffs([(0, 0), (w, 0), (w, h), (0, h)], src_quad)
    return img.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=90)


def _coeffs(dst, src):
    m = []
    for (x, y), (u, v) in zip(dst, src):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    return np.linalg.solve(np.array(m, float), np.array(src, float).reshape(8)).tolist()


def light(img):
    w, h = img.size
    gx = np.linspace(0.65, 1.05, w)[None, :] * np.linspace(1.0, 0.8, h)[:, None]
    a = np.asarray(img, dtype=np.float32) * gx + 18
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


truth = {}
for name, path, page, tess in SOURCES:
    doc = pdfium.PdfDocument(path)
    page = min(page, len(doc))
    pg = doc[page - 1]
    truth_text = ordered_page(pg)
    for kind, dpi in (("scan", 200), ("photo", 170), ("poor", 100)):
        img = pg.render(scale=dpi / 72, grayscale=True).to_pil().convert("L")
        if kind == "scan":
            img = jpeg(noise(skew(img, 0.6), 6), 60)
        elif kind == "photo":
            img = jpeg(light(perspective(img)).filter(ImageFilter.GaussianBlur(0.8)), 80)
        else:
            img = jpeg(noise(skew(img, 2.5).filter(ImageFilter.GaussianBlur(1.1)), 22), 35)
        fid = f"{name}-{kind}"
        if kind == "photo":
            img.convert("RGB").save(f"{OUT}/fixtures/{fid}.jpg", quality=80)
            fpath = f"ocr/fixtures/{fid}.jpg"
        else:
            img.save(f"{OUT}/fixtures/{fid}.pdf", "PDF", resolution=dpi)
            fpath = f"ocr/fixtures/{fid}.pdf"
        truth[fid] = {"file": fpath, "source": os.path.relpath(path, D), "page": page, "tess_lang": tess,
                      "kind": kind, "dpi": dpi, "text": truth_text}
json.dump(truth, open(f"{OUT}/truth.json", "w"), ensure_ascii=False, indent=1)
json.dump(manifest, open(f"{OUT}/sources.json", "w"), ensure_ascii=False, indent=1)
print(len(truth), "fixtures")
