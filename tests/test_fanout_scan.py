"""`scripts/fanout_scan.py` and `scripts/fanout_scan_eval.py`: prompts, regions, parsing and scoring.

No model server is reached: the scan is driven by a stand-in client that answers from a table,
and the case miner runs on a scratch git repository.
"""

import importlib.util
import itertools
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    """Imports one of the scripts as a module."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fs = load("fanout_scan")
ev = load("fanout_scan_eval")


class FakeClient:
    """Answers chat requests by a rule on the last question; counts requests; no server."""

    def __init__(self, rule):
        self.rule = rule
        self.seen = []

    def chat(self, msgs, max_tokens):
        self.seen.append(msgs)
        return self.rule(msgs[-1]["content"])

    def cache_counters(self):
        return {"prefill_cache": 900.0 * len(self.seen), "prefill_compute": 100.0}


def python_source(functions, body_lines=30):
    """A Python file of `functions` functions of `body_lines` lines each."""
    out = ['"""Module."""', ""]
    for index in range(functions):
        out.append(f"def f{index}(x):")
        out.extend(f"    x = x + {line}" for line in range(body_lines - 2))
        out.append("    return x")
        out.append("")
    return "\n".join(out) + "\n"


# Prompt building


def test_every_probe_of_a_file_shares_the_same_prefix():
    source = python_source(8)
    numbered = fs.number_lines(source)
    plan = fs.plan_probes("pkg/mod.py", source)
    prefixes = {json.dumps(fs.messages("pkg/mod.py", numbered, q)[:-1]) for _, _, q in plan}
    assert len(plan) > 10 and len(prefixes) == 1
    assert fs.BASELINE_QUESTION not in prefixes.pop()


def test_lines_are_numbered_from_one_as_the_answers_cite_them():
    assert fs.number_lines("a\nb\n") == "1| a\n2| b"


def test_a_short_file_is_asked_about_as_a_whole():
    plan = fs.plan_probes("m.py", "import os\nprint(os.getcwd())\n")
    assert plan and all(region.name == "file" for _, region, _ in plan)
    assert all("anywhere in the file" in question for _, _, question in plan)


def test_a_long_file_names_the_lines_of_each_region():
    plan = fs.plan_probes("m.py", python_source(8))
    assert all(f"lines {r.start}-{r.end} only" in q for _, r, q in plan)


def test_classes_follow_language_tests_and_triggers():
    keys = {c.key for c in fs.classes_for("src/lib.rs", "fn main() {}\n")}
    assert "unwrap-panic" in keys and "none-misuse" not in keys and "subprocess-misuse" not in keys
    assert "test-real-machine" not in keys
    assert "test-real-machine" in {c.key for c in fs.classes_for("tests/test_x.py", "x = 1\n")}
    assert "subprocess-misuse" in {c.key for c in fs.classes_for("a.py", "import subprocess\n")}
    assert fs.classes_for("notes.txt", "text") == []


def test_the_checklist_is_large_and_its_keys_are_unique():
    keys = [c.key for c in fs.CHECKLIST]
    assert 30 <= len(keys) <= 60 and len(keys) == len(set(keys))


def test_a_confirm_question_quotes_the_candidate():
    finding = fs.Finding(42, "file never closed", "resource-leak", "f")
    for framing in fs.CONFIRM_FRAMINGS:
        question = fs.confirm_question(framing, finding)
        assert "42" in question and "file never closed" in question
        assert question.endswith(fs.CONFIRM_FORMAT) and "verdict:" in question


# Region splitting


def test_regions_cover_every_line_once_in_order():
    source = python_source(12)
    total = len(source.splitlines())
    regions = fs.split_regions(source, "py")
    assert regions[0].start == 1 and regions[-1].end == total
    assert all(b.start == a.end + 1 for a, b in itertools.pairwise(regions))


def test_small_python_functions_are_merged_up_to_about_eighty_lines():
    regions = fs.split_regions(python_source(12, body_lines=20), "py")
    assert all(r.end - r.start + 1 <= fs.REGION_LINES + 10 for r in regions)
    assert any("+" in r.name for r in regions)


def test_a_long_function_is_cut_into_windows():
    regions = fs.split_regions(python_source(1, body_lines=400), "py")
    assert len(regions) >= 4 and all(r.end - r.start + 1 <= fs.REGION_LINES * 2 for r in regions)


def test_a_long_class_is_split_into_its_methods():
    lines = ["class Big:"]
    for index in range(6):
        lines.append(f"    def m{index}(self):")
        lines.extend("        pass" for _ in range(30))
    regions = fs.split_regions("\n".join(lines) + "\n", "py")
    assert any(r.name.startswith("Big.m") for r in regions)


def test_regions_are_merged_down_to_the_cap():
    regions = fs.split_regions(python_source(60), "py", max_regions=5)
    assert len(regions) == 5
    assert regions[0].start == 1 and regions[-1].end == len(python_source(60).splitlines())


def test_rust_items_are_found_by_brace_depth_and_a_long_impl_by_its_fns():
    body = "\n".join("        let x = 1;" for _ in range(40))
    source = (
        "use std::fs;\n\npub struct S { a: u32 }\n\nimpl S {\n"
        + "".join(f"    pub fn m{i}(&self) {{\n{body}\n    }}\n" for i in range(4))
        + '}\n\nfn helper() -> &\'static str {\n    "}"\n}\n'
    )
    regions = fs.split_regions(source, "rs")
    names = " ".join(r.name for r in regions)
    assert "impl S::m1" in names and "fn helper" in names
    assert regions[-1].end == len(source.splitlines())


def test_unparsable_python_falls_back_to_windows():
    source = "def broken(:\n" + "x = 1\n" * 300
    regions = fs.split_regions(source, "py")
    assert (
        regions[0].start == 1
        and regions[-1].end == 301
        and all(r.name.startswith("lines") for r in regions)
    )


# Answer parsing


@pytest.mark.parametrize(
    "answer, line",
    [
        ("yes L59 response object not closed", 59),
        ("Yes, L7: open without close", 7),
        ("`yes L12 x`", 12),
        ("yes 30 missing timeout", 30),
    ],
)
def test_yes_answers_name_their_line(answer, line):
    finding = fs.parse_answer(answer, "resource-leak", fs.Region(1, 99, "file"))
    assert finding is not None and finding.line == line and finding.kind == "resource-leak"


@pytest.mark.parametrize("answer", ["no", "No.", "", "yes", "maybe L3", "I think there is none"])
def test_other_answers_are_no_finding(answer):
    assert fs.parse_answer(answer, "k", fs.Region(1, 9, "file")) is None


@pytest.mark.parametrize(
    "answer, verdict",
    [
        ("real high", (True, "high")),
        ("Real, medium.", (True, "medium")),
        ("real", (True, "")),
        ("no", (False, "")),
        ("not a bug", (False, "")),
        ("unsure", (False, "")),
        ("If the host is empty the name is wrong; not handled.\nverdict: real low", (True, "low")),
        ("The claim is real only if X, which cannot happen. verdict: no", (False, "")),
    ],
)
def test_verdicts(answer, verdict):
    assert fs.parse_verdict(answer) == verdict


def test_a_majority_of_framings_confirms_and_names_the_median_severity():
    finding = fs.tally(fs.Finding(3, "t", "k", "f"), ["real high", "real low", "no"])
    assert finding.confirmed and finding.severity in ("high", "low")
    assert not fs.tally(fs.Finding(3, "t", "k", "f"), ["real high", "no", "no"]).confirmed


def test_the_baseline_list_is_read_in_its_common_shapes():
    answer = "1. L12 foo bar\n- **L30-L33** baz\nL7: qux\nLine 9 - quux\nsome prose\nnone"
    assert [f.line for f in fs.parse_baseline(answer)] == [12, 30, 7, 9]


def test_dedupe_merges_neighbours_and_drops_lines_outside_the_file():
    raw = [
        fs.Finding(12, "b", "leak", "r"),
        fs.Finding(10, "a", "leak", "r"),
        fs.Finding(11, "c", "race", "r"),
        fs.Finding(30, "f", "race", "r"),
        fs.Finding(500, "d", "leak", "r"),
        fs.Finding(0, "e", "leak", "r"),
    ]
    kept = fs.dedupe(raw, 100)
    assert [(f.line, f.text, f.kind) for f in kept] == [(12, "b", "leak+race"), (30, "f", "race")]


def test_dedupe_keeps_the_candidates_most_classes_agree_on():
    raw = [fs.Finding(line, "x", f"k{line}", "r") for line in range(1, 100, 10)]
    raw += [fs.Finding(52, "y", "other", "r"), fs.Finding(91, "z", "third", "r")]
    kept = fs.dedupe(raw, 200, limit=2)
    assert [f.line for f in kept] == [51, 91] and kept[0].kind == "k51+other"


def test_metrics_are_summed_by_mode_and_give_a_hit_rate():
    text = (
        'sglang:realtime_tokens_total{engine_type="unified",mode="prefill_compute",tp_rank="0"} 100.0\n'
        'sglang:realtime_tokens_total{engine_type="unified",mode="prefill_cache",tp_rank="0"} 1.5e+03\n'
        '# HELP other\nsglang:prompt_tokens_total{a="b"} 9\n'
    )
    before = fs.parse_metrics(text)
    assert before == {"prefill_compute": 100.0, "prefill_cache": 1500.0}
    after = {"prefill_compute": 200.0, "prefill_cache": 2400.0}
    assert fs.hit_rate(before, after) == 0.9
    assert fs.hit_rate({}, after) is None


# The scan, driven by a stand-in


def test_scan_runs_both_rounds_and_keeps_the_confirmed():
    source = "import os\n\n\ndef read(p):\n    f = open(p)\n    return f.read()\n"

    def rule(question):
        if question.startswith("Problem:"):
            return "yes L5 file never closed" if "resource" in question else "no"
        return "real medium" if "L5" in question or "line 5" in question else "no"

    client = FakeClient(rule)
    result = fs.scan(client, "m.py", source, concurrency=2)
    assert [f["line"] for f in result["findings"]] == [5]
    assert result["findings"][0]["severity"] == "medium"
    summary = result["summary"]
    assert summary["probes_round2"] == len(fs.CONFIRM_FRAMINGS)
    assert summary["probes_round1"] + summary["probes_round2"] == len(client.seen)
    assert summary["cache_hit_rate"] is not None


def test_scan_drops_a_candidate_the_framings_reject():
    client = FakeClient(lambda q: "yes L1 wrong thing" if q.startswith("Problem:") else "no")
    result = fs.scan(client, "m.py", "x = 1\n", concurrency=1)
    assert result["findings"] == [] and result["summary"]["candidates"] == 1


def test_the_baseline_is_one_call_over_the_same_prefix():
    client = FakeClient(lambda q: "L2 bad\nL99 outside")
    result = fs.baseline(client, "m.py", "a = 1\nb = 2\n")
    assert len(client.seen) == 1 and [f["line"] for f in result["findings"]] == [2]
    assert client.seen[0][:-1] == fs.messages("m.py", fs.number_lines("a = 1\nb = 2\n"), "")[:-1]


# Scoring and the case miner


def test_line_matching_allows_three_lines_and_five_at_an_insertion():
    ranges = [{"start": 20, "end": 22, "insert": 0}, {"start": 50, "end": 51, "insert": 1}]
    assert ev.matches(17, ranges) and ev.matches(25, ranges) and not ev.matches(26, ranges)
    assert ev.matches(56, ranges) and not ev.matches(57, ranges)
    assert ev.ranges_text(ranges) == "20-22, 50-51"


def test_old_ranges_read_a_zero_context_diff():
    diff = "@@ -10,3 +10,4 @@ def f\n-a\n@@ -40 +41 @@\n-b\n@@ -60,0 +62,2 @@\n+c\n"
    assert ev.old_ranges(diff) == [
        {"start": 10, "end": 12, "insert": 0},
        {"start": 40, "end": 40, "insert": 0},
        {"start": 60, "end": 61, "insert": 1},
    ]


def test_only_removed_code_counts_as_deleted():
    diff = "--- a/x.py\n-    # comment\n-\n-    return a - b\n+    return a + b\n"
    assert ev.deleted_code_lines(diff) == 1


def test_recall_flags_by_line_and_judge():
    case = {"ranges": [{"start": 5, "end": 5, "insert": 0}]}
    findings = [{"line": 5, "text": "x"}, {"line": 90, "text": "y"}]
    assert ev.recall_flags(case, findings, None) == {"line": True}
    assert ev.recall_flags(case, findings, [False, True]) == {
        "line": True,
        "judge": True,
        "both": False,
    }


def git(repo, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, env=env)


@pytest.fixture
def history(tmp_path):
    """A scratch repository: a feature, then a fix with a test, then a docs change."""
    repo = str(tmp_path / "repo")
    pathlib.Path(repo, "tests").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    with pathlib.Path(repo, "calc.py").open("w") as handle:
        handle.write("def add(a, b):\n    return a - b\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Calculator: add two numbers")
    with pathlib.Path(repo, "calc.py").open("w") as handle:
        handle.write("def add(a, b):\n    return a + b\n")
    with pathlib.Path(repo, "tests", "test_calc.py").open("w") as handle:
        handle.write("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Calculator: add returned the difference instead of the sum")
    with pathlib.Path(repo, "README").open("w") as handle:
        handle.write("calc\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "README: describe the calculator")
    return repo


def test_the_miner_keeps_the_fix_with_its_old_lines_and_both_files(history, tmp_path):
    out = str(tmp_path / "out")
    document = ev.mine(history, "main", out, 10)
    assert [c["path"] for c in document["cases"]] == ["calc.py"]
    case = document["cases"][0]
    assert case["ranges"] == [{"start": 2, "end": 2, "insert": 0}] and case["tests"] == [
        "tests/test_calc.py"
    ]
    folder = pathlib.Path(out, "cases", case["sha"])
    assert "a - b" in (folder / "before").read_text()
    assert "a + b" in (folder / "after").read_text()
    assert document["stages"]["commits"] == 3 and document["classifier"] == "subject"


def test_the_miner_asks_the_classifier_when_given_one(history, tmp_path):
    asked = []
    document = ev.mine(
        history, "main", str(tmp_path / "out"), 10, lambda s, d: asked.append(s) or False
    )
    assert document["cases"] == [] and asked == [
        "Calculator: add returned the difference instead of the sum"
    ]
    assert document["stages"]["classified_fix"] == 0


def test_score_counts_recall_and_samples_after_findings(tmp_path):
    out = str(tmp_path)
    pathlib.Path(out, "cases", "abc").mkdir(parents=True)
    with pathlib.Path(out, "cases", "abc", "before").open("w") as handle:
        handle.write("x\n" * 50)
    case = {
        "sha": "abc",
        "path": "m.py",
        "lines_before": 50,
        "subject": "s",
        "split": "eval",
        "ranges": [{"start": 10, "end": 10, "insert": 0}],
    }
    with pathlib.Path(out, "cases.json").open("w") as handle:
        json.dump({"cases": [case, {**case, "sha": "dev", "split": "dev"}]}, handle)
    summary = {
        "seconds": 2.0,
        "probes_round1": 40,
        "probes_round2": 6,
        "cache_hit_rate": 0.9,
        "candidates": 3,
    }
    rows = [
        {
            "sha": "abc",
            "side": "before",
            "method": "fanout",
            "summary": summary,
            "findings": [{"line": 11, "text": "a", "severity": "low"}],
            "candidates": [{"line": 11}, {"line": 30}],
        },
        {
            "sha": "abc",
            "side": "before",
            "method": "baseline",
            "summary": {**summary, "probes_round1": 1},
            "findings": [{"line": 40, "text": "b"}],
            "candidates": [],
        },
        {
            "sha": "abc",
            "side": "after",
            "method": "fanout",
            "summary": summary,
            "findings": [{"line": 3, "text": "c"}],
            "candidates": [],
        },
    ]
    with pathlib.Path(out, "runs.jsonl").open("w") as handle:
        handle.write("".join(json.dumps(r) + "\n" for r in rows))
    document = ev.score(out, None, "eval")
    methods = document["methods"]
    assert methods["fanout"]["recall_line"] == 1.0 and methods["baseline"]["recall_line"] == 0.0
    assert methods["fanout"]["probes_per_file"] == 46 and methods["fanout"]["recall_judge"] is None
    assert methods["fanout_round1"]["recall_line"] == 1.0
    assert methods["fanout_medium"] == {"recall_line": 0.0, "findings_per_file": 0}
    assert methods["after_findings"] == {"fanout": 1, "baseline": 0}
    sample = [
        json.loads(line)
        for line in pathlib.Path(out, "precision_sample.jsonl").read_text().splitlines()
    ]
    assert [s["line"] for s in sample] == [3] and sample[0]["grade"] == ""


def test_load_runs_makes_the_eval_resumable(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text(json.dumps({"sha": "a", "side": "before", "method": "fanout"}) + "\n\n")
    assert set(ev.load_runs(str(path))) == {("a", "before", "fanout")}
    assert ev.load_runs(str(tmp_path / "missing.jsonl")) == {}
