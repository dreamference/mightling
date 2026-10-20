"""Phase 3 (spec §19): the five CSV tables of the set as one Excel workbook, a sheet each.

    make_xlsx.py <corpus>      # writes <corpus>/xlsx/tables.xlsx and questions-xlsx.jsonl here

Written with the standard library (SpreadsheetML by hand): whole numbers as numbers, every other
value as an inline string, so a value reads as the CSV wrote it (`23000.0` stays `23000.0`). The
five CSV questions are copied with the workbook as their document; the acceptance run leaves the
CSV files out (`csv/` in the corpus's `.lingignore`), so the workbook answers for them.
"""

import csv
import json
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def column(index: int) -> str:
    """Excel's column letters for a 0-based index."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def cell(ref: str, value: str) -> str:
    """One cell: a whole number as a number, anything else as an inline string."""
    if value.isdigit() and not value.startswith("0"):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'


def sheet(rows: list) -> str:
    """A worksheet's XML."""
    body = []
    for r, row in enumerate(rows, 1):
        cells = "".join(cell(f"{column(c)}{r}", v) for c, v in enumerate(row) if v != "")
        body.append(f'<row r="{r}">{cells}</row>')
    return f'<worksheet xmlns="{MAIN}"><sheetData>{"".join(body)}</sheetData></worksheet>'


def main() -> None:
    """Writes the workbook and the remapped questions."""
    corpus = Path(sys.argv[1])
    tables = sorted((corpus / "csv").glob("*.csv"))
    out = corpus / "xlsx" / "tables.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheets = "".join(
        f'<sheet name="{t.stem}" sheetId="{i}" r:id="rId{i}"/>' for i, t in enumerate(tables, 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(tables) + 1)
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as book:
        root = f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/>'
        book.writestr("_rels/.rels", f'<Relationships xmlns="{PKG}">{root}</Relationships>')
        book.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>{sheets}</sheets></workbook>',
        )
        book.writestr(
            "xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{PKG}">{rels}</Relationships>'
        )
        for i, table in enumerate(tables, 1):
            with table.open(newline="", encoding="utf-8") as handle:
                book.writestr(f"xl/worksheets/sheet{i}.xml", sheet(list(csv.reader(handle))))
    with (HERE / "questions.jsonl").open() as lines:
        questions = [json.loads(line) for line in lines]
    moved = [
        {**q, "doc": "xlsx/tables.xlsx", "loc": f"sheet {Path(q['doc']).stem}, {q['loc']}"}
        for q in questions
        if q["doc"].startswith("csv/")
    ]
    (HERE / "questions-xlsx.jsonl").write_text("".join(json.dumps(q) + "\n" for q in moved))
    sys.stdout.write(f"{out}: {len(tables)} sheets; {len(moved)} questions\n")


if __name__ == "__main__":
    main()
