"""Phase 3 acceptance (spec §12, §19): spreadsheets, on the whole evaluation set.

    make_xlsx.py corpus && phase3_acceptance.py <ling-docs wrapper> <out.json>

The set has no spreadsheet of its own, so make_xlsx.py writes its five CSV tables into one workbook,
a sheet each, and copies their five questions with the workbook as the document. The wrapper's
collection is `corpus/` with `csv/` (and `_docx_src/`) in its `.lingignore`, so the workbook stands
in for the CSV files and every other document is as in Phase 2. Scored as phase1_acceptance.py
scores, over all 181 questions and over the workbook's five.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from phase1_acceptance import bench, score  # noqa: E402

CSV_FOLDER = "csv"


def main() -> None:
    """The set's questions with the CSV ones moved to the workbook, scored, written to <out.json>."""
    wrapper, out = sys.argv[1], sys.argv[2]
    with (HERE / "questions.jsonl").open() as lines:
        questions = [q for q in map(json.loads, lines) if q["doc"].split("/")[0] != CSV_FOLDER]
    with (HERE / "questions-xlsx.jsonl").open() as lines:
        workbook = [json.loads(line) for line in lines]
    questions += workbook
    answers = {
        merge: {a["id"]: a for a in bench(wrapper, questions, merge=merge)}
        for merge in (True, False)
    }

    def both(subset: list) -> dict:
        return {
            "merged": score(subset, [answers[True][q["id"]] for q in subset]),
            "per_chunk": score(subset, [answers[False][q["id"]] for q in subset]),
        }

    result = {"questions": len(questions), "all": both(questions), "workbook": both(workbook)}
    Path(out).write_text(json.dumps(result, indent=1))
    sys.stdout.write(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
