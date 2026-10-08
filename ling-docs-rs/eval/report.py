"""Score every configuration that has been embedded and every PDF engine's chunks with BM25, write
results/retrieval.jsonl and print Markdown tables for the spec."""
import glob
import json
import os
import subprocess
import sys

D = os.path.dirname(os.path.abspath(__file__))
rows = []
for chunks in sorted(glob.glob(f"{D}/chunks/*.json")):
    tag = os.path.splitext(os.path.basename(chunks))[0]
    prefixes = sorted(p[:-5] for p in glob.glob(f"{D}/emb/{tag}__*.json"))
    out = subprocess.run([sys.executable, f"{D}/evaluate.py", chunks, *prefixes], capture_output=True, text=True,
                         check=True).stdout
    rows += [json.loads(line) for line in out.splitlines() if line.startswith("{")]
with open(f"{D}/results/retrieval.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
for p in glob.glob(f"{D}/emb/*.json"):
    info = json.load(open(p))
    json.dump(info, open(f"{D}/results/embed-{os.path.basename(p)}", "w"), indent=1)


def frac(s):
    a, b = s.split("/")
    return f"{int(a) / int(b):.2f}"


print("| chunks | method | R@10 | R@5 | MRR | doc R@10 | lexical | paraphrase | ceiling |")
print("|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print(f"| {r['chunks']} | {r['method']} | {r['recall10']:.3f} | {r['recall5']:.3f} | {r['mrr10']:.3f} | "
          f"{r['doc_recall10']:.3f} | {r['by_kind']['lexical']} | {r['by_kind']['paraphrase']} | {r['ceiling']} |")
print()
print("| model | dim | chunks/s (4 cores) | query embed p50/p95 ms | peak RSS MB |")
print("|---|---|---|---|---|")
seen = set()
for r in rows:
    if r["chunks"] == "pdfium-512" and r["method"].startswith("dense:") and r["method"] not in seen:
        seen.add(r["method"])
        print(f"| {r['method'][6:]} | | {r['embed_chunks_per_second']} | {r['query_embed_ms_p50']}/"
              f"{r['query_embed_ms_p95']} | {r['model_rss_mb']} |")
print()
for r in rows:
    if r["chunks"] == "pdfium-512":
        print(r["method"], "by format:", {k: v for k, v in r["by_format"].items()})
