"""`scripts/swe_bench_chooser_test.py`: the chooser test on disk (backlog item 1), against a fake server on loopback."""

import dataclasses
import importlib.util
import json
import os
import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDS = ["acme__a-1", "acme__a-2", "acme__a-3", "acme__a-4", "acme__a-5"]
MODEL = "mightling/fake"


@pytest.fixture
def chooser():
    spec = importlib.util.spec_from_file_location("swe_bench_chooser_test",
                                                  os.path.join(ROOT, "scripts", "swe_bench_chooser_test.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up while they are built
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture
def server():
    """A model server that rates a patch containing GOOD 9 and any other 3."""
    seen, state = [], {"refuse": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, code=200):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply({"data": [{"id": "fake-model"}]} if self.path == "/v1/models" else {})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            if state["refuse"]:
                return self.reply({"error": "the benchmark has the model"}, 503)
            text = payload["messages"][1]["content"]
            a = text.split("<solution_A>")[1].split("</solution_A>")[0]
            b = text.split("<solution_B>")[1].split("</solution_B>")[0]
            rate = lambda patch: 9 if "GOOD" in patch else 3
            self.reply({"choices": [{"message": {"content": f"Reasoning.\nRATING_A: {rate(a)}\nRATING_B: {rate(b)}"}}]})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{httpd.server_address[1]}", "seen": seen, "state": state}
    httpd.shutdown()


def write_run(root, name, patches, results, reports=None):
    """A run on disk; `reports` writes the harness's report.json (instance -> tests_status) for some."""
    directory = root / name
    (directory / "eval" / "1").mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"name": name, "instances": IDS, "model_name_or_path": MODEL}))
    (directory / "predictions.jsonl").write_text("".join(
        json.dumps({"instance_id": i, "model_name_or_path": MODEL, "model_patch": patches[i]}) + "\n" for i in IDS)
        + '{"instance_id": "torn')
    (directory / "eval" / "1" / "grading.json").write_text(json.dumps({"grader": {}, "results": results}))
    for instance_id, status in (reports or {}).items():
        path = directory / "eval" / "1" / "logs" / "run_evaluation" / f"{name}-1" / MODEL.replace("/", "__") / instance_id
        path.mkdir(parents=True)
        (path / "report.json").write_text(json.dumps({instance_id: {"resolved": False, "tests_status": status}}))


def verdict(resolved, p2p_failed=None, applied=True):
    result = {"resolved": resolved, "applied": applied}
    if p2p_failed is not None:
        result.update(f2p=1, f2p_failed=0 if resolved else 1, p2p=4, p2p_failed=p2p_failed)
    return result


@pytest.fixture
def disk(tmp_path):
    """
    a-1: every run resolved (not judged). a-2: r2's GOOD patch resolved, r1 and r3 wrote the same
    other patch. a-3: r1 resolved; r2's breaks a PASS_TO_PASS test (in grading.json), r3's is empty.
    a-4: no run resolved. a-5: r1 resolved; r2's GOOD patch did not; r3's breaks a PASS_TO_PASS
    test (in report.json only), and its FAIL_TO_PASS-only failure in r1's report must not drop r1's.
    """
    runs = tmp_path / "runs"
    p2p_broken = {"FAIL_TO_PASS": {"success": [], "failure": ["f"]}, "PASS_TO_PASS": {"success": ["p"], "failure": ["q"]}}
    f2p_only = {"FAIL_TO_PASS": {"success": [], "failure": ["f"]}, "PASS_TO_PASS": {"success": ["p"], "failure": []}}
    write_run(runs, "r1", {"acme__a-1": "p1", "acme__a-2": "bad 1", "acme__a-3": "x1", "acme__a-4": "", "acme__a-5": "y1"},
              {"acme__a-1": verdict(True, 0), "acme__a-2": verdict(False, 0), "acme__a-3": verdict(True, 0),
               "acme__a-4": {"resolved": False, "empty": True}, "acme__a-5": verdict(True)},
              reports={"acme__a-5": f2p_only})
    write_run(runs, "r2", {"acme__a-1": "p1", "acme__a-2": "GOOD", "acme__a-3": "x2", "acme__a-4": "n", "acme__a-5": "GOOD y2"},
              {"acme__a-1": verdict(True, 0), "acme__a-2": verdict(True, 0), "acme__a-3": verdict(False, 2),
               "acme__a-4": verdict(False, 0), "acme__a-5": verdict(False, 0)})
    write_run(runs, "r3", {"acme__a-1": "p1b", "acme__a-2": "bad 1\n", "acme__a-3": "", "acme__a-4": "n", "acme__a-5": "z"},
              {"acme__a-1": verdict(True, 0), "acme__a-2": verdict(False, 0), "acme__a-3": {"resolved": False, "empty": True},
               "acme__a-4": verdict(False, 0), "acme__a-5": verdict(False)},
              reports={"acme__a-5": p2p_broken})
    dataset = tmp_path / "verified.jsonl"
    dataset.write_text("".join(json.dumps({"instance_id": i, "problem_statement": f"issue {i}"}) + "\n" for i in IDS))
    return {"runs": runs, "dataset": dataset, "out": tmp_path / "out"}


def args(chooser, disk, *extra):
    return chooser.parser().parse_args(["--runs", "r1,r2,r3", "--runs-dir", str(disk["runs"]),
                                        "--dataset", str(disk["dataset"]), "--out", str(disk["out"]), *extra])


def test_the_tasks_where_the_runs_disagree_and_what_the_regression_filter_may_know(chooser, disk):
    runs = [chooser.load_run(disk["runs"], name) for name in ("r1", "r2", "r3")]
    tasks = {t.instance_id: t for t in chooser.select_tasks(runs)}
    assert sorted(tasks) == ["acme__a-2", "acme__a-3", "acme__a-5"]
    # The same text up to trailing whitespace is one candidate; an empty patch is none.
    assert [c.runs for c in tasks["acme__a-2"].candidates] == [["r1", "r3"], ["r2"]]
    assert [(c.runs, c.breaks) for c in tasks["acme__a-3"].candidates] == [(["r1"], False), (["r2"], True)]
    # From report.json: r3's PASS_TO_PASS failure drops it; r1's FAIL_TO_PASS failure does not.
    a5 = {c.runs[0]: c for c in tasks["acme__a-5"].candidates}
    assert (a5["r1"].p2p_failed, a5["r1"].breaks) == (0, False)
    assert (a5["r3"].p2p_failed, a5["r3"].breaks) == (1, True)
    assert len(chooser.select_tasks(runs, every=True)) == 5


def test_the_chooser_is_never_given_a_verdict_on_the_issue(chooser):
    fields = {f.name for f in dataclasses.fields(chooser.Candidate)}
    assert fields == {"id", "patch", "runs", "applied", "p2p_failed"}


def test_a_dry_run_contacts_no_server_and_writes_nothing(chooser, disk, capsys):
    assert chooser.run_test(args(chooser, disk, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "3 task(s) from r1, r2, r3; 2 go to the judge, 2 judge call(s) at most" in out
    assert "[r2] breaks 2 PASS_TO_PASS: dropped" in out and "resolved" not in out
    assert not disk["out"].exists()


def test_the_filter_then_the_pairwise_judge_scored_against_the_baselines_and_resumed(chooser, disk, server, capsys):
    assert chooser.run_test(args(chooser, disk, "--server", server["url"], "--seed", "7")) == 0
    seen = server["seen"]
    # a-2 and a-5 each have two survivors: one call each. a-3 is decided by the filter.
    assert len(seen) == 2 and all(p["model"] == "fake-model" and p["seed"] == 7 for p in seen)
    shown = json.dumps(seen)
    assert "issue acme__a-2" in shown and "RATING_A" in shown
    assert "resolved" not in shown and "FAIL_TO_PASS" not in shown and "PASS_TO_PASS" not in shown
    summary = json.loads((disk["out"] / "summary.json").read_text())
    per_task = summary["per_task"]
    assert per_task["acme__a-2"]["stage"] == "judge" and per_task["acme__a-2"]["chooser"] == 1.0
    assert per_task["acme__a-3"]["stage"] == "regression filter" and per_task["acme__a-3"]["chooser"] == 1.0
    # a-5: the judge prefers the GOOD patch, which did not resolve; the filter alone is a coin (0.5).
    assert per_task["acme__a-5"]["chooser"] == 0.0 and per_task["acme__a-5"]["filter"] == 0.5
    assert (summary["chooser"], summary["filter_alone"], summary["random_run"], summary["oracle"]) == (2.0, 2.0, 1.0, 3)
    assert summary["stages"] == {"judge": 2, "regression filter": 1}
    assert summary["single_runs"] == {"r1": 3, "r2": 2, "r3": 1} and summary["projected"] == 3.0
    assert "chooser 2.0, the regression filter alone 2.0, a random run 1.0" in capsys.readouterr().out
    # Run again: every pair is already judged, so nothing is asked.
    assert chooser.run_test(args(chooser, disk, "--server", server["url"], "--seed", "7")) == 0
    assert len(server["seen"]) == 2


def test_a_refusing_server_stops_the_test(chooser, disk, server):
    server["state"]["refuse"] = True
    with pytest.raises(SystemExit, match="answered 503"):
        chooser.run_test(args(chooser, disk, "--server", server["url"]))


def test_v1s_tournament_compares_everyone_twice_then_swiss_and_weights_by_the_rating_gap(chooser):
    candidates = [chooser.Candidate(id=f"c{n}", patch=f"patch {n}", runs=[f"r{n}"]) for n in range(5)]
    calls = []

    def compare(a, b):
        calls.append(frozenset((a.id, b.id)))
        return (int(a.id[1]) + 1, int(b.id[1]) + 1)  # c4 is best, rated 5 against c0's 1

    points = chooser.tournament(candidates, compare, random.Random(1), swiss_rounds=1)
    assert len(calls) == len(set(calls)), "no pair is played twice"
    counts = {c.id: sum(1 for pair in calls if c.id in pair) for c in candidates}
    assert min(counts.values()) >= 2
    assert max(points, key=points.get) == "c4"
    # Three candidates: the three pairs, nothing more.
    calls.clear()
    chooser.tournament(candidates[:3], compare, random.Random(1))
    assert len(calls) == 3
    # The weight: a gap of 9 is worth 1, a tie the floor shared, an unreadable answer the same.
    two = candidates[:2]
    rate = {"c0": 10, "c1": 1}
    assert chooser.tournament(two, lambda a, b: (rate[a.id], rate[b.id]), random.Random(0)) == {"c0": 1.0, "c1": 0.0}
    assert chooser.tournament(two, lambda a, b: (5, 5), random.Random(0)) == {"c0": 0.05, "c1": 0.05}
    assert chooser.tournament(two, lambda a, b: None, random.Random(0)) == {"c0": 0.05, "c1": 0.05}


def test_when_every_candidate_breaks_a_test_all_go_to_the_judge(chooser):
    broken = [chooser.Candidate(id=i, patch=i, runs=[i], p2p_failed=1) for i in ("a", "GOOD")]
    choice = chooser.choose(broken, lambda a, b: (9 if "GOOD" in a.patch else 3, 9 if "GOOD" in b.patch else 3),
                            random.Random(0))
    assert choice["stage"] == "judge, all broke" and choice["chosen"] == ["GOOD"]
    assert chooser.choose([], None, random.Random(0))["stage"] == "none"


def test_ratings_are_read_from_the_last_lines(chooser):
    assert chooser.ratings("RATING_A: 2\nRATING_B: 4 ... on reflection\nRATING_A: **8**\nRATING_B: 12") == (8.0, 10.0)
    assert chooser.ratings("RATING_A: 3") is None
    assert chooser.clip("abcdef", 3).startswith("abc\n[... cut: 3 more")
    assert chooser.Client("http://node.local:8000/v1/").base == "http://node.local:8000/v1"
