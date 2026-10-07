"""Fetch the OCR models once, outside the sandbox (RapidOCR otherwise downloads from ModelScope at first
use: a network call ling-docs must never make; it ships the files instead)."""
import os
import subprocess

from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

D = os.path.dirname(os.path.abspath(__file__))
q = {"Global.log_level": "warning"}
for mt in (ModelType.TINY, ModelType.SMALL, ModelType.MEDIUM):
    RapidOCR(params={**q, "Det.model_type": mt, "Rec.model_type": mt})
for lang in (LangRec.LATIN, LangRec.ESLAV, LangRec.CYRILLIC, LangRec.CH, LangRec.KOREAN, LangRec.ARABIC):
    RapidOCR(params={**q, "Det.ocr_version": OCRVersion.PPOCRV5, "Det.lang_type": LangDet.CH,
                     "Det.model_type": ModelType.MOBILE, "Rec.ocr_version": OCRVersion.PPOCRV5,
                     "Rec.lang_type": lang, "Rec.model_type": ModelType.MOBILE})
    print("ok", lang)
for lang in ("jpn", "kor", "ara", "ukr"):
    p = f"{D}/tess/tessdata/{lang}.traineddata"
    if not os.path.exists(p):
        subprocess.run(["curl", "-sL", "-o", p, f"https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata"],
                       check=True)
mdir = os.path.join(os.path.dirname(__import__("rapidocr").__file__), "models")
for f in sorted(os.listdir(mdir)):
    print(f"{os.path.getsize(os.path.join(mdir, f)) / 2**20:7.1f} MB  {f}")
