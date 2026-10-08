"""Summarise the OCR runs: ocr_report.py -> results/ocr-summary.json.
1. Word recall by script group and image quality, seconds per page and peak memory, per engine.
2. Model sizes on disk.
3. Picking the recognition model per page: script detection by Tesseract OSD, by best confidence over
   every PP-OCRv5 model, and "PP-OCRv6 first, the other scripts' models only below a confidence
   threshold", each replayed from v5-all's per-model runs with its real cost.
4. Whether confidence can route pages to Qwen3.8: per-word ROC AUC (confidence vs the word being in
   the page's text), and a page-level threshold sweep against pages read badly (recall < 0.8)."""
import collections
import glob
import json
import os
import statistics

import rapidocr

D = os.path.dirname(os.path.abspath(__file__))
R = f"{D}/results"
GROUP = {"eng": "Latin", "deu": "Latin", "swe": "Latin", "fra": "Latin", "rus": "Cyrillic", "ukr": "Cyrillic",
         "chi_sim": "CJK", "jpn": "CJK", "kor": "Hangul", "ara": "Arabic"}
V5_OF = {"Latin": "Latin", "Cyrillic": "Cyrillic", "Han": "Han", "Japanese": "Han", "Hangul": "Hangul",
         "Arabic": "Arabic"}
OSD_TO = {"Latin": "Latin", "Cyrillic": "Cyrillic", "Han": "Han", "HanS": "Han", "HanT": "Han",
          "Japanese": "Han", "Hangul": "Hangul", "Korean": "Hangul", "Arabic": "Arabic"}
BAD = 0.8  # a page read with word recall below this is "bad": the one Qwen3.8 should see


def load(name):
    p = f"{R}/ocr-{name}.json"
    return json.load(open(p)) if os.path.exists(p) else None


def mean(xs):
    return round(statistics.mean(xs), 3) if xs else None


def table(pages, recall_of, secs_of):
    cells = collections.defaultdict(list)
    for p in pages.values():
        cells[(GROUP[p["lang"]], p["kind"])].append(recall_of(p))
    out = {}
    for g in ("Latin", "Cyrillic", "CJK", "Hangul", "Arabic"):
        out[g] = {k: mean(cells[(g, k)]) for k in ("scan", "photo", "poor") if cells[(g, k)]}
    good = [recall_of(p) for p in pages.values() if p["kind"] != "poor"]
    return {"recall_by_script": out, "recall_scan_photo_all_scripts": mean(good),
            "recall_poor_all_scripts": mean([recall_of(p) for p in pages.values() if p["kind"] == "poor"]),
            "seconds_per_page_mean": mean([secs_of(p) for p in pages.values()]),
            "seconds_per_page_max": round(max(secs_of(p) for p in pages.values()), 2)}


def auc(pos, neg):
    """Probability that a correct word's confidence exceeds a wrong word's (Mann-Whitney)."""
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks, i = {}, 0
    rank_sum = 0.0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        r = (i + j + 1) / 2
        rank_sum += r * sum(1 for k in range(i, j) if allv[k][1] == 1)
        i = j
    return round((rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), 3)


def sweep(rows):
    """rows: (page_conf, recall). For each threshold: bad pages caught, good pages sent needlessly."""
    bad = [r for r in rows if r[1] < BAD]
    good = [r for r in rows if r[1] >= BAD]
    out = {}
    for t in (0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.96):
        out[f"{t:.2f}"] = {"bad_caught": f"{sum(1 for c, _ in bad if c < t)}/{len(bad)}",
                           "good_sent": f"{sum(1 for c, _ in good if c < t)}/{len(good)}"}
    return out


summary = {"note": "word recall: multiset of the source text layer's words (characters for Chinese, Japanese and "
                   "Korean) found in the OCR output; Arabic truth in logical order (after the bidi fix); 39 pages = 13 sources x scan (200 dpi), phone photo "
                   "(170 dpi, perspective, shading, blur, JPEG) and poor (100 dpi, noise, low contrast)",
           "engines": {}}
for name in ("v6-tiny", "v6-small", "v6-medium", "v5-oracle", "tesseract"):
    d = load(name)
    if d:
        summary["engines"][name] = {"peak_rss_mb": d["peak_rss_mb"],
                                    **table(d["pages"], lambda p: p["word_recall"], lambda p: p["seconds"])}

# Model sizes on disk (what ships).
mdir = os.path.join(os.path.dirname(rapidocr.__file__), "models")
sizes = {f: round(os.path.getsize(os.path.join(mdir, f)) / 2**20, 1) for f in sorted(os.listdir(mdir))
         if f.endswith(".onnx")}
tess = {os.path.basename(f): round(os.path.getsize(f) / 2**20, 1) for f in sorted(glob.glob(f"{D}/tess/tessdata/*"))}
summary["model_mb"] = {"rapidocr": sizes, "tessdata_fast": tess,
                       "rapidocr_v5_mobile_all_scripts_mb": round(sum(v for k, v in sizes.items() if "PP-OCRv5" in k
                                                                      or "cls" in k), 1),
                       "rapidocr_v6_small_mb": round(sum(v for k, v in sizes.items() if "v6" in k and "small" in k), 1)}

v5 = load("v5-all")
v6 = load("v6-small")
osd = load("osd")
sel = {}
if v5:
    def pick(models, rule):
        if rule == "mean_conf":
            return max(models, key=lambda k: models[k]["mean_conf"])
        return max(models, key=lambda k: models[k]["mean_conf"] * models[k]["n_words"])

    for rule in ("mean_conf", "conf_mass"):
        right, rec, secs = 0, {}, []
        for fid, p in v5["pages"].items():
            k = pick(p["models"], rule)
            right += k == V5_OF[p["script"]]
            rec[fid] = p["models"][k]["word_recall"]
            secs.append(sum(m["seconds"] for m in p["models"].values()))
        pages = {fid: dict(p, r=rec[fid], s=0) for fid, p in v5["pages"].items()}
        sel[f"best-of-5 by {rule}"] = {"script_right": f"{right}/{len(rec)}", "seconds_per_page_mean": mean(secs),
                                      **{k: v for k, v in table(pages, lambda p: p["r"], lambda p: 0).items()
                                         if k.startswith("recall")}}
    oracle = {fid: dict(p, r=p["models"][V5_OF[p["script"]]]["word_recall"],
                        s=p["models"][V5_OF[p["script"]]]["seconds"]) for fid, p in v5["pages"].items()}
    sel["oracle (real script's v5 model)"] = {k: v for k, v in table(oracle, lambda p: p["r"], lambda p: p["s"]).items()}
    if osd:
        right, pages = 0, {}
        for fid, p in v5["pages"].items():
            det = OSD_TO.get(osd["pages"][fid]["detected_script"], "Latin")
            right += det == V5_OF[p["script"]]
            m = p["models"][det]
            pages[fid] = dict(p, r=m["word_recall"], s=m["seconds"] + osd["pages"][fid]["seconds"])
        sel["Tesseract OSD then v5"] = {"script_right": f"{right}/{len(pages)}",
                                        "osd_seconds_mean": mean([o["seconds"] for o in osd["pages"].values()]),
                                        "osd_detected": {fid: o["detected_script"] for fid, o in osd["pages"].items()},
                                        **table(pages, lambda p: p["r"], lambda p: p["s"])}
        if v6:
            # The recommended route: OSD; PP-OCRv6 small for Latin and CJK (one model, better on poor
            # pages), the PP-OCRv5 model of the script otherwise.
            pages, route_rows = {}, []
            for fid, p in v5["pages"].items():
                det = OSD_TO.get(osd["pages"][fid]["detected_script"], "Latin")
                o = osd["pages"][fid]["seconds"]
                if det in ("Latin", "Han"):
                    q = v6["pages"][fid]
                    pages[fid] = dict(p, r=q["word_recall"], s=q["seconds"] + o)
                    route_rows.append((statistics.mean([s for s, _ in q["words"]]) if q["words"] else 0.0,
                                       q["word_recall"]))
                else:
                    m = p["models"][det]
                    pages[fid] = dict(p, r=m["word_recall"], s=m["seconds"] + o)
                    route_rows.append((m["mean_conf"], m["word_recall"]))
            sel["Tesseract OSD, then v6-small (Latin, CJK) or the v5 script model"] = {
                "script_right": sel["Tesseract OSD then v5"]["script_right"],
                **table(pages, lambda p: p["r"], lambda p: p["s"])}
    if v6:
        for t in (0.80, 0.85, 0.90, 0.93):
            pages, fell = {}, 0
            for fid, p in v5["pages"].items():
                q = v6["pages"][fid]
                conf = statistics.mean([s for s, _ in q["words"]]) if q["words"] else 0.0
                if conf >= t:
                    pages[fid] = dict(p, r=q["word_recall"], s=q["seconds"])
                    continue
                fell += 1
                alts = {k: p["models"][k] for k in ("Cyrillic", "Hangul", "Arabic")}
                best = max(alts, key=lambda k: alts[k]["mean_conf"] * alts[k]["n_words"])
                own = (q["word_recall"], conf * len(q["words"]))
                if alts[best]["mean_conf"] * alts[best]["n_words"] > own[1]:
                    r = alts[best]["word_recall"]
                else:
                    r = q["word_recall"]
                pages[fid] = dict(p, r=r, s=q["seconds"] + sum(a["seconds"] for a in alts.values()))
            sel[f"v6-small, fallback below mean confidence {t:.2f}"] = {
                "pages_falling_back": f"{fell}/{len(pages)}", **table(pages, lambda p: p["r"], lambda p: p["s"])}
summary["model_selection"] = sel

# Confidence as the "send to Qwen3.8" signal.
conf = {}
for name in ("v6-small", "v5-oracle", "tesseract"):
    d = load(name)
    if not d:
        continue
    pos = [s for p in d["pages"].values() for s, ok in p["words"] if ok]
    neg = [s for p in d["pages"].values() for s, ok in p["words"] if not ok]
    rows = [(statistics.mean([s for s, _ in p["words"]]) if p["words"] else 0.0, p["word_recall"])
            for p in d["pages"].values()]
    conf[name] = {"word_auc": auc(pos, neg), "words_right": len(pos), "words_wrong": len(neg),
                  "page_threshold_sweep": sweep(rows)}
if v5:
    rows = [(p["models"][V5_OF[p["script"]]]["mean_conf"], p["models"][V5_OF[p["script"]]]["word_recall"])
            for p in v5["pages"].values()]
    conf["v5 (right script's model)"] = {"page_threshold_sweep": sweep(rows)}
if v5 and v6 and osd:
    conf["recommended route (OSD, v6-small or v5 script model)"] = {"page_threshold_sweep": sweep(route_rows)}
summary["confidence_routing"] = conf
json.dump(summary, open(f"{R}/ocr-summary.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(summary, indent=1, ensure_ascii=False)[:6000])
