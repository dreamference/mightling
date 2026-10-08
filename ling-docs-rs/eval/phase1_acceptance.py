"""Phase 1 acceptance (spec §12, §16): ling-docs itself, end to end, on the text and PDF part of the
evaluation set.

    phase1_acceptance.py <ling-docs wrapper> <out.json> [--scale <wrapper for a 50k-chunk home>]

The wrapper runs the built `ling-docs` with a throwaway HOME whose only collection is this
directory's `corpus/` (ling-docs reads its PDF, Markdown, reStructuredText and text files and
leaves the rest unopened). `bench-search` answers every question in one process, with the model
and the vectors loaded once, as the MCP server does, and records the ranked passages and each
query's wall time. A hit is the expected document with the answer snippet inside one of the first
10 (or 5) passages, the test of Phase 0 (`evallib.is_hit`). Reported twice: with adjacent chunks
merged, as ling-docs answers, and per chunk, as Phase 0 counted.
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
import evallib  # noqa: E402

PHASE1_FORMATS = ("pdf", "md", "rst", "txt")


def bench(wrapper, questions, merge=True):
    with tempfile.TemporaryDirectory() as tmp:
        qpath, out = os.path.join(tmp, "q.jsonl"), os.path.join(tmp, "out.jsonl")
        with open(qpath, "w") as f:
            f.write("\n".join(json.dumps(q) for q in questions))
        args = ["bash", wrapper, "bench-search", qpath, out, "--k", "10"] + ([] if merge else ["--no-merge"])
        subprocess.run(args, check=True)
        return [json.loads(line) for line in open(out)]


def score(questions, answers):
    by_id = {a["id"]: a for a in answers}
    hits5 = hits10 = 0
    rr = 0.0
    kinds = {}
    for q in questions:
        hits = by_id[q["id"]]["hits"]
        first = next((r for r, h in enumerate(hits[:10]) if h["rel"] == q["doc"] and evallib.is_hit(h["text"], q["snippet"])), None)
        hits10 += first is not None
        hits5 += first is not None and first < 5
        rr += 1.0 / (first + 1) if first is not None else 0.0
        k = kinds.setdefault(q["doc"].split("/")[0] + "-" + q["kind"], [0, 0])
        k[0] += first is not None
        k[1] += 1
    ms = [by_id[q["id"]]["ms"] for q in questions]
    n = len(questions)
    return {"n": n, "recall5": round(hits5 / n, 3), "recall10": round(hits10 / n, 3), "mrr10": round(rr / n, 3),
            "by_group": {k: f"{a}/{b}" for k, (a, b) in sorted(kinds.items())},
            "query_ms_p50": round(float(np.percentile(ms, 50)), 1), "query_ms_p95": round(float(np.percentile(ms, 95)), 1)}


def main():
    wrapper, out = sys.argv[1], sys.argv[2]
    scale = sys.argv[sys.argv.index("--scale") + 1] if "--scale" in sys.argv else None
    questions = [json.loads(line) for line in open(f"{D}/questions.jsonl")]
    phase1 = [q for q in questions if q["doc"].split("/")[0] in PHASE1_FORMATS]
    result = {"questions": len(phase1),
              "merged": score(phase1, bench(wrapper, phase1, merge=True)),
              "per_chunk": score(phase1, bench(wrapper, phase1, merge=False))}
    if scale:
        # Latency only: every question against the 50k-chunk home.
        answers = bench(scale, questions, merge=True)
        ms = [a["ms"] for a in answers]
        result["scale_50k"] = {"queries": len(ms), "query_ms_p50": round(float(np.percentile(ms, 50)), 1),
                               "query_ms_p95": round(float(np.percentile(ms, 95)), 1)}
    json.dump(result, open(out, "w"), indent=1)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
