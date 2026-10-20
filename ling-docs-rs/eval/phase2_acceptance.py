"""Phase 2 acceptance (spec §12, §18): ling-docs itself, end to end, on the whole evaluation set.

ling-docs now reads HTML, DOCX, e-mail and CSV as well as text and PDF.

    phase2_acceptance.py <ling-docs wrapper> <out.json>

The wrapper runs the built `ling-docs` with a throwaway HOME whose only collection is this
directory's `corpus/`, indexed by `ling-docs index` (every file of the set is read now). Scored as
phase1_acceptance.py scores (the expected document with the answer snippet inside one of the first
10 or 5 passages), merged and per chunk, over all 181 questions, over the questions about the
formats Phase 2 added, and without the cross-lingual questions, which are reported and not gated
(§15.3).
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from phase1_acceptance import bench, score  # noqa: E402

PHASE2_FORMATS = ("html", "html-multi", "docx", "email", "csv")
CROSS_LINGUAL = "crosslingual"


def main() -> None:
    """Index-and-search results for every question, scored by subset, written to <out.json>."""
    wrapper, out = sys.argv[1], sys.argv[2]
    with (HERE / "questions.jsonl").open() as lines:
        questions = [json.loads(line) for line in lines]
    merged = {a["id"]: a for a in bench(wrapper, questions, merge=True)}
    per_chunk = {a["id"]: a for a in bench(wrapper, questions, merge=False)}

    def both(subset: list) -> dict:
        return {
            "merged": score(subset, [merged[q["id"]] for q in subset]),
            "per_chunk": score(subset, [per_chunk[q["id"]] for q in subset]),
        }

    result = {
        "questions": len(questions),
        "all": both(questions),
        "without_cross_lingual": both([q for q in questions if q["kind"] != CROSS_LINGUAL]),
        "phase2_formats": both([q for q in questions if q["doc"].split("/")[0] in PHASE2_FORMATS]),
        "cross_lingual": both([q for q in questions if q["kind"] == CROSS_LINGUAL]),
    }
    Path(out).write_text(json.dumps(result, indent=1))
    sys.stdout.write(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
