#!/usr/bin/env python3
"""Measures `fanout_scan.py` on this repository's own fixed bugs, against a one-call baseline.

    fanout_scan_eval.py cases --out DIR [--repo ROOT] [--rev origin/main] [--limit N]
    fanout_scan_eval.py run   --out DIR [--host URL] [--concurrency N] [--split eval|dev|all]
    fanout_scan_eval.py score --out DIR [--host URL] [--no-judge]

`cases` mines the history for bug-fix commits that changed one to three Python or Rust source
files a little, usually with a test: the subject says what was broken or fixed. For each it keeps
the primary file before the fix (`cases/<sha>/before`) and after it (`after`), and the old-side
line ranges the fix touched, which is where the known bug is. It writes `cases.json` with the
count left after each filter. A few cases are set aside as `dev` for tuning the prompts and are
never scored.

`run` scans each case's before and after files with the fan-out and the baseline and appends
one JSON line per scan to `runs.jsonl`; a scan already there is skipped, so a run that stops can
be started again. `score` reads both and writes `score.json` and `precision_sample.jsonl`: thirty
confirmed findings from the after files, for grading by hand.

Recall counts a case found when a finding's line is within three lines of the fix's old-side
ranges (five of a pure insertion), and, with the judge, when the model on the same server agrees
that a finding describes the commit's bug. Nothing here writes to the repository.
"""

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from typing import Any, Final

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pathlib

import fanout_scan as fs

SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"not a crash|instead of|no longer|\bfix(es|ed)?\b|\bbug\b|broken|wrong|crash|never |is refused|"
    r"fails? closed|left behind|leak|race|hang|stale|escap|unescaped|off.by.one|missing",
    re.IGNORECASE,
)
NOT_A_FIX_RE: Final[re.Pattern[str]] = re.compile(
    r"\bruff\b|\bmypy\b|clippy|code standard|format(ting)?\b|typing|lint|rename|ratchet|\bspec\b|"
    r"docs?:|README|changelog|release notes|bump|revert",
    re.IGNORECASE,
)
SKIP_PREFIXES: Final[tuple[str, ...]] = ("codex/", "ling-engine/", "codex-tests/", "codex-patches/")
MAX_SOURCE_FILES: Final[int] = 3
MAX_CHANGED_LINES: Final[int] = 120
MAX_FILE_LINES: Final[int] = 1500
LINE_SLACK: Final[int] = 3
INSERT_SLACK: Final[int] = 5
DEV_SHARE: Final[int] = 12  # one case in twelve, by hash, is kept for tuning
PRECISION_SAMPLE: Final[int] = 30
HUNK_RE: Final[re.Pattern[str]] = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")

CLASSIFY_SYSTEM: Final[str] = (
    "You sort commits of a software project. A defect fix changes code that behaved wrongly (a crash, "
    "a wrong result, a leak, a hang, a security hole, a broken edge case) so that it behaves rightly. "
    "A new feature, a refactor, a test-only change, a docs or style change or a tuning of defaults is "
    "not a defect fix. Answer `yes` or `no`."
)
CLASSIFY_DIFF_LINES: Final[int] = 120
MIN_SUBJECT: Final[int] = 12

JUDGE_QUESTION: Final[str] = (
    "A later commit fixed a bug in this file. Its message: `{subject}`. It changed lines {ranges}.\n"
    "A reviewer reported: `L{line} {text}`.\nDoes the report describe the bug that commit fixed, not "
    "some other problem? Answer `yes` or `no`."
)


def git(repo: str, *args: str) -> str:
    """Runs one git command in the repository and returns its output."""
    return subprocess.run(
        ["git", "-C", repo, *args], check=True, capture_output=True, text=True
    ).stdout


def is_source(path: str) -> bool:
    """A Python or Rust file outside the tests, the submodules and the patch folders."""
    return (
        fs.language_of(path) != ""
        and not fs.is_test_file(path)
        and not path.startswith(SKIP_PREFIXES)
    )


def old_ranges(diff: str) -> list[dict[str, int]]:
    """The old-side ranges of a `-U0` diff's hunks; a pure insertion is the line it follows, marked."""
    ranges = []
    for line in diff.splitlines():
        match = HUNK_RE.match(line)
        if match:
            start, count = (
                int(match.group(1)),
                int(match.group(2) if match.group(2) is not None else 1),
            )
            if count == 0:
                ranges.append({"start": max(1, start), "end": start + 1, "insert": 1})
            else:
                ranges.append({"start": start, "end": start + count - 1, "insert": 0})
    return ranges


def deleted_code_lines(diff: str) -> int:
    """Counts removed lines that are code: not blank, not a comment, not a docstring line."""
    count = 0
    for line in diff.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            text = line[1:].strip()
            if text and not text.startswith(("#", "//", '"""', "'''", "///", "//!")):
                count += 1
    return count


def is_dev(sha: str) -> bool:
    """Whether a case is set aside for tuning, by a stable hash of its commit."""
    return int(hashlib.sha256(sha.encode()).hexdigest(), 16) % DEV_SHARE == 0


def classify_question(subject: str, diff: str) -> str:
    """The classifier's question about one commit: its message and the start of its diff."""
    shown = "\n".join(diff.splitlines()[:CLASSIFY_DIFF_LINES])
    return f"Commit message: {subject}\n\nDiff of its source files:\n{shown}\n\nIs this commit a defect fix?"


def model_classifier(client: Any) -> Any:
    """A classifier that asks the model whether a commit fixes a defect."""

    def classify(subject: str, diff: str) -> bool:
        answer = client.chat(
            [
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": classify_question(subject, diff)},
            ],
            4,
        )
        return answer.strip().lower().startswith("yes")

    return classify


def mine(repo: str, rev: str, out: str, limit: int, classify: Any = None) -> dict[str, Any]:
    """Builds the case set from the history and writes `cases.json` and the case files under `out`.

    Args:
        repo: The repository.
        rev: The branch whose history is mined.
        out: The output directory.
        limit: The most cases to keep, newest first.
        classify: `(subject, diff) -> bool`, whether a commit fixes a defect; None to go by the
            subject's words alone (`SUBJECT_RE`).

    Returns:
        The written document: the cases and the count left after each filter.
    """
    log = git(repo, "log", rev, "--no-merges", "--format=%H%x09%s")
    stages = {
        "commits": 0,
        "subject": 0,
        "source_files": 0,
        "small": 0,
        "deletes_code": 0,
        "has_test": 0,
    }
    stages.update({"size": 0, "classified_fix": 0} if classify else {"size": 0})
    cases = []
    for entry in log.splitlines():
        sha, _, subject = entry.partition("\t")
        stages["commits"] += 1
        if len(subject) < MIN_SUBJECT or NOT_A_FIX_RE.search(subject):
            continue
        if classify is None and not SUBJECT_RE.search(subject):
            continue
        stages["subject"] += 1
        numstat = git(repo, "show", "--numstat", "--format=", "--diff-filter=AMDR", "-M", sha)
        files = []
        for row in numstat.splitlines():
            added, deleted, path = (row.split("\t") + ["", ""])[:3]
            if added == "-":
                continue
            files.append((path, int(added), int(deleted)))
        sources = [f for f in files if is_source(f[0])]
        tests = [f[0] for f in files if fs.is_test_file(f[0]) and fs.language_of(f[0])]
        if not 1 <= len(sources) <= MAX_SOURCE_FILES or any("=>" in f[0] for f in sources):
            continue
        status = git(repo, "show", "--name-status", "--format=", sha)
        if any(
            row.startswith(("A", "D", "R")) and row.split("\t")[-1] in {s[0] for s in sources}
            for row in status.splitlines()
        ):
            continue
        stages["source_files"] += 1
        if sum(a + d for _, a, d in sources) > MAX_CHANGED_LINES:
            continue
        stages["small"] += 1
        path = max(sources, key=lambda f: f[1] + f[2])[0]
        diff = git(repo, "diff", "-U0", f"{sha}^", sha, "--", path)
        if deleted_code_lines(diff) == 0:
            continue
        stages["deletes_code"] += 1
        if not tests:
            continue
        stages["has_test"] += 1
        before = git(repo, "show", f"{sha}^:{path}")
        if len(before.splitlines()) > MAX_FILE_LINES:
            continue
        stages["size"] += 1
        if classify is not None:
            source_diff = git(repo, "diff", f"{sha}^", sha, "--", *[s[0] for s in sources])
            if not classify(subject, source_diff):
                continue
            stages["classified_fix"] += 1
        after = git(repo, "show", f"{sha}:{path}")
        folder = os.path.join(out, "cases", sha[:12])
        pathlib.Path(folder).mkdir(exist_ok=True, parents=True)
        for name, text in (("before", before), ("after", after)):
            with pathlib.Path(os.path.join(folder, name)).open("w", encoding="utf-8") as handle:
                handle.write(text)
        cases.append(
            {
                "sha": sha[:12],
                "subject": subject,
                "path": path,
                "lang": fs.language_of(path),
                "lines_before": len(before.splitlines()),
                "ranges": old_ranges(diff),
                "tests": tests,
                "other_sources": [s[0] for s in sources if s[0] != path],
                "split": "dev" if is_dev(sha) else "eval",
            }
        )
        if len(cases) >= limit:
            break
    document = {
        "rev": git(repo, "rev-parse", "--short", rev).strip(),
        "classifier": "model" if classify else "subject",
        "stages": stages,
        "cases": cases,
    }
    pathlib.Path(out).mkdir(exist_ok=True, parents=True)
    with pathlib.Path(os.path.join(out, "cases.json")).open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1)
    return document


def matches(line: int, ranges: list[dict[str, int]]) -> bool:
    """Whether a finding's line falls on the fix: within three lines of a range, five of an insertion."""
    for r in ranges:
        slack = INSERT_SLACK if r.get("insert") else LINE_SLACK
        if r["start"] - slack <= line <= r["end"] + slack:
            return True
    return False


def ranges_text(ranges: list[dict[str, int]]) -> str:
    """The fix's ranges as the judge reads them, e.g. `12-14, 30`."""
    return ", ".join(
        str(r["start"]) if r["start"] == r["end"] else f"{r['start']}-{r['end']}" for r in ranges
    )


def load_runs(path: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    """The scans already in `runs.jsonl`, by (case, side, method)."""
    runs: dict[tuple[str, str, str], dict[str, Any]] = {}
    if pathlib.Path(path).exists():
        with pathlib.Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    runs[(row["sha"], row["side"], row["method"])] = row
    return runs


def selected(cases: list[dict[str, Any]], split: str) -> list[dict[str, Any]]:
    """The cases of one split (`eval`, `dev` or `all`)."""
    return [c for c in cases if split == "all" or c["split"] == split]


def run(out: str, host: str, concurrency: int, split: str, sides: tuple[str, ...]) -> int:
    """Scans every selected case not yet in `runs.jsonl`; returns how many scans it ran."""
    with pathlib.Path(os.path.join(out, "cases.json")).open(encoding="utf-8") as handle:
        cases = selected(json.load(handle)["cases"], split)
    runs_path = os.path.join(out, "runs.jsonl")
    done = load_runs(runs_path)
    client = fs.Client(host)
    count = 0
    for case in cases:
        for side in sides:
            with pathlib.Path(os.path.join(out, "cases", case["sha"], side)).open(encoding="utf-8") as handle:
                source = handle.read()
            for method in ("fanout", "baseline"):
                if (case["sha"], side, method) in done:
                    continue
                if method == "fanout":
                    result = fs.scan(client, case["path"], source, concurrency)
                else:
                    result = fs.baseline(client, case["path"], source)
                row = {"sha": case["sha"], "side": side, "method": method, **result}
                with pathlib.Path(runs_path).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
                count += 1
                s = result["summary"]
                print(
                    f"{case['sha']} {side:6s} {method:8s} {s['lines']:5d} lines {s['confirmed']:3d} found "
                    f"{s['probes_round1'] + s['probes_round2']:4d} probes {s['seconds']:7.1f} s",
                    flush=True,
                )
    return count


def judge(client: Any, case: dict[str, Any], source: str, finding: dict[str, Any]) -> bool:
    """Asks the model whether a finding describes the case's known bug, over the before file's prefix."""
    question = JUDGE_QUESTION.format(
        subject=case["subject"],
        ranges=ranges_text(case["ranges"]),
        line=finding["line"],
        text=finding["text"],
    )
    answer = client.chat(fs.messages(case["path"], fs.number_lines(source), question), 4)
    return answer.strip().lower().startswith("yes")


def recall_flags(
    case: dict[str, Any], findings: list[dict[str, Any]], judged: list[bool] | None
) -> dict[str, bool]:
    """Whether a case's bug was found: by line, by the judge, and by both on one finding."""
    by_line = [matches(f["line"], case["ranges"]) for f in findings]
    flags = {"line": any(by_line)}
    if judged is not None:
        flags["judge"] = any(judged)
        flags["both"] = any(a and b for a, b in zip(by_line, judged))
    return flags


def rate(values: list[bool]) -> float | None:
    """The share of true values, or None of none."""
    return round(sum(values) / len(values), 3) if values else None


def mean(values: list[float]) -> float | None:
    """The mean, or None of no values."""
    return round(sum(values) / len(values), 3) if values else None


def score(out: str, host: str | None, split: str, seed: int = 7) -> dict[str, Any]:
    """Scores the runs: recall of the known bugs, findings per file, time, probes and the cache.

    Args:
        out: The output directory with `cases.json` and `runs.jsonl`.
        host: The model server for the judge, or None to score by line only.
        split: The split scored, `eval` normally.
        seed: The seed of the precision sample.

    Returns:
        The document written to `score.json`.
    """
    with pathlib.Path(os.path.join(out, "cases.json")).open(encoding="utf-8") as handle:
        cases = selected(json.load(handle)["cases"], split)
    runs = load_runs(os.path.join(out, "runs.jsonl"))
    client = fs.Client(host) if host else None
    per_case, methods = [], {}
    for case in cases:
        with pathlib.Path(os.path.join(out, "cases", case["sha"], "before")).open(encoding="utf-8") as handle:
            source = handle.read()
        row: dict[str, Any] = {
            "sha": case["sha"],
            "path": case["path"],
            "lines": case["lines_before"],
        }
        for method in ("fanout", "baseline"):
            scan = runs.get((case["sha"], "before", method))
            if scan is None:
                continue
            findings = scan["findings"]
            judged = [judge(client, case, source, f) for f in findings] if client else None
            row[method] = recall_flags(case, findings, judged)
            row[method]["findings"] = len(findings)
            if method == "fanout":
                row["fanout_round1"] = recall_flags(case, scan["candidates"], None)
                serious = [f for f in findings if f.get("severity") in ("high", "medium")]
                row["fanout_medium"] = recall_flags(case, serious, None)
                row["fanout_medium"]["findings"] = len(serious)
            row[method + "_summary"] = scan["summary"]
        per_case.append(row)
    for method in ("fanout", "baseline"):
        rows = [r for r in per_case if method in r]
        summaries = [r[method + "_summary"] for r in rows]
        methods[method] = {
            "cases": len(rows),
            "recall_line": rate([r[method]["line"] for r in rows]),
            "recall_judge": rate([r[method]["judge"] for r in rows if "judge" in r[method]]),
            "recall_both": rate([r[method]["both"] for r in rows if "both" in r[method]]),
            "findings_per_file": mean([r[method]["findings"] for r in rows]),
            "findings_per_100_lines": mean(
                [100 * r[method]["findings"] / max(1, r["lines"]) for r in rows]
            ),
            "seconds_per_file": mean([s["seconds"] for s in summaries]),
            "probes_per_file": mean([s["probes_round1"] + s["probes_round2"] for s in summaries]),
            "cache_hit_rate": mean(
                [s["cache_hit_rate"] for s in summaries if s.get("cache_hit_rate") is not None]
            ),
        }
    methods["fanout_round1"] = {
        "recall_line": rate([r["fanout_round1"]["line"] for r in per_case if "fanout_round1" in r]),
        "candidates_per_file": mean(
            [r["fanout_summary"]["candidates"] for r in per_case if "fanout_summary" in r]
        ),
    }
    methods["fanout_medium"] = {
        "recall_line": rate([r["fanout_medium"]["line"] for r in per_case if "fanout_medium" in r]),
        "findings_per_file": mean(
            [r["fanout_medium"]["findings"] for r in per_case if "fanout_medium" in r]
        ),
    }
    after = [
        {"sha": c["sha"], "path": c["path"], "method": m, **f}
        for c in cases
        for m in ("fanout", "baseline")
        if (scan := runs.get((c["sha"], "after", m)))
        for f in scan["findings"]
    ]
    methods["after_findings"] = {
        m: sum(1 for f in after if f["method"] == m) for m in ("fanout", "baseline")
    }
    sample: list[dict[str, Any]] = []
    for m in ("fanout", "baseline"):
        pool = [f for f in after if f["method"] == m]
        random.Random(seed).shuffle(pool)
        sample.extend(pool[:PRECISION_SAMPLE])
    with pathlib.Path(os.path.join(out, "precision_sample.jsonl")).open("w", encoding="utf-8") as handle:
        for finding in sample:
            handle.write(json.dumps({**finding, "grade": ""}) + "\n")
    document = {"split": split, "judge": bool(client), "methods": methods, "cases": per_case}
    with pathlib.Path(os.path.join(out, "score.json")).open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1)
    return document


def main(argv: list[str] | None = None) -> int:
    """Parses the command line and runs one of `cases`, `run` and `score`."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    host = os.environ.get("DREAMFERENCE_VLLM_HOST") or fs.DEFAULT_HOST
    p_cases = sub.add_parser("cases")
    p_cases.add_argument("--out", required=True)
    p_cases.add_argument("--repo", default=".")
    p_cases.add_argument("--rev", default="origin/main")
    p_cases.add_argument("--limit", type=int, default=110)
    p_cases.add_argument("--host", default=host)
    p_cases.add_argument(
        "--by-subject", action="store_true", help="no model: keep commits by their words"
    )
    p_run = sub.add_parser("run")
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--host", default=host)
    p_run.add_argument("--concurrency", type=int, default=8)
    p_run.add_argument("--split", default="eval", choices=("eval", "dev", "all"))
    p_run.add_argument("--before-only", action="store_true")
    p_score = sub.add_parser("score")
    p_score.add_argument("--out", required=True)
    p_score.add_argument("--host", default=host)
    p_score.add_argument("--split", default="eval", choices=("eval", "dev", "all"))
    p_score.add_argument("--no-judge", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "cases":
        classify = None if args.by_subject else model_classifier(fs.Client(args.host))
        document = mine(args.repo, args.rev, args.out, args.limit, classify)
        print(json.dumps(document["stages"]))
        print(
            f"{sum(c['split'] == 'eval' for c in document['cases'])} eval, "
            f"{sum(c['split'] == 'dev' for c in document['cases'])} dev"
        )
    elif args.command == "run":
        sides = ("before",) if args.before_only else ("before", "after")
        print(f"{run(args.out, args.host, args.concurrency, args.split, sides)} scans")
    else:
        document = score(args.out, None if args.no_judge else args.host, args.split)
        print(json.dumps(document["methods"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
