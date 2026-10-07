"""Score every embedded configuration of the multilingual pass and collect the embedding runs:
retrieval_report.py -> results/retrieval.jsonl (one row per chunk set x method) and
results/embedding-candidates.json (speed, memory and quality per model, full runs and probes).
Prints Markdown tables for the spec."""
import glob
import json
import os
import subprocess
import sys

D = os.path.dirname(os.path.abspath(__file__))
rows = []
for chunks in sorted(glob.glob(f"{D}/chunks/pdfium-ordered-*.json")):
    tag = os.path.splitext(os.path.basename(chunks))[0]
    prefixes = sorted(p[:-5] for p in glob.glob(f"{D}/emb/{tag}__*.json"))
    if not prefixes:
        continue
    out = subprocess.run([sys.executable, f"{D}/evaluate.py", chunks, *prefixes], capture_output=True, text=True,
                         check=True).stdout
    rows += [json.loads(line) for line in out.splitlines() if line.startswith("{")]
with open(f"{D}/results/retrieval.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

models = {}
for p in sorted(glob.glob(f"{D}/emb/*.json")):
    info = json.load(open(p))
    if "licence" not in info:
        continue  # the English-only runs before the multilingual decision
    base = os.path.basename(p)
    if base.startswith("batch"):
        key = f"probe-batch-{info['batch']}"
    else:
        key = "probe" if info.get("probe") else f"full-{info['chunk_tokens']}"
    keep = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in info.items() if k not in ("model",)}
    models.setdefault(info["model"], {})[key] = keep
for r in rows:
    if r["method"].startswith(("dense:", "hybrid:")):
        name = r["method"].split(":", 1)[1]
        for m in models:
            if m.split("/")[-1] == name:
                models[m].setdefault("quality", {})[f"{r['chunks']} {r['method'].split(':')[0]}"] = {
                    k: (round(r[k], 3) if isinstance(r[k], float) else r[k])
                    for k in ("recall10", "recall5", "mrr10", "doc_recall10", "by_kind", "by_lang", "by_group")}
json.dump(models, open(f"{D}/results/embedding-candidates.json", "w"), indent=1, ensure_ascii=False)


def frac(s):
    a, b = s.split("/")
    return f"{int(a) / int(b):.2f}"


print("| chunks | method | R@10 | R@5 | MRR@10 | en lexical (54) | en paraphrase (55) | other-language lexical (24) "
      "| other-language paraphrase (24) | cross-lingual (24) |")
print("|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    g = r["by_group"]
    cells = " | ".join(g.get(k, "-").split("/")[0] for k in ("en-lexical", "en-paraphrase", "other-lexical",
                                                            "other-paraphrase", "other-crosslingual"))
    print(f"| {r['chunks'].replace('pdfium-ordered-', '')} | {r['method']} | {r['recall10']:.3f} | {r['recall5']:.3f} | "
          f"{r['mrr10']:.3f} | {cells} |")
print()
print("| model | licence | dim | chunks/s, 4 cores (full run) | chunks/s (probe) | query p50/p95 ms | peak RSS MB |")
print("|---|---|---|---|---|---|---|")
for m, v in models.items():
    full = v.get("full-512", {})
    pr = v.get("probe", {})
    any_ = full or pr
    print(f"| {m} | {any_.get('licence')} | {any_.get('dim')} | {full.get('chunks_per_second', '')} | "
          f"{pr.get('chunks_per_second', '')} | {any_.get('query_ms_p50')}/{any_.get('query_ms_p95')} | "
          f"{max(full.get('maxrss_mb', 0), pr.get('maxrss_mb', 0))} |")
