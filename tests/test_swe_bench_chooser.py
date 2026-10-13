"""`scripts/swe_bench_chooser_test.py`: the chooser test on disk (backlog item 1), against a fake server on loopback."""

import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDS = ["acme__a-1", "acme__a-2", "acme__a-3", "acme__a-4"]


@pytest.fixture
def chooser():
    spec = importlib.util.spec_from_file_location("swe_bench_chooser_test",
                                                  os.path.join(ROOT, "scripts", "swe_bench_chooser_test.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server():
    """A model server that prefers the patch containing GOOD, and cannot decide between two without it."""
    seen = []

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
            self.reply({"data": [{"id": "fake-model"}]} if self.path == "/v1/models" else {}, 200)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            if server_state["refuse"]:
                return self.reply({"error": "the benchmark has the model"}, 503)
            text = payload["messages"][1]["content"]
            a = text.split("<patch_A>")[1].split("</patch_A>")[0]
            b = text.split("<patch_B>")[1].split("</patch_B>")[0]
            answer = "WINNER: A" if "GOOD" in a else "WINNER: B" if "GOOD" in b else "I cannot tell."
            self.reply({"choices": [{"message": {"content": f"Reasoning.\n{answer}"}}]})

    server_state = {"refuse": False}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{httpd.server_address[1]}", "seen": seen, "state": server_state}
    httpd.shutdown()


def write_run(root, name, patches, resolved):
    directory = root / name
    (directory / "eval" / "1").mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"name": name, "instances": IDS}))
    (directory / "predictions.jsonl").write_text("".join(
        json.dumps({"instance_id": i, "model_name_or_path": "m", "model_patch": patches[i]}) + "\n" for i in IDS)
        + '{"instance_id": "torn')
    (directory / "eval" / "1" / "grading.json").write_text(json.dumps(
        {"grader": {}, "results": {i: {"resolved": resolved[i]} for i in IDS}}))


@pytest.fixture
def disk(tmp_path):
    """Three runs: a-1 resolved by all; a-2 only r2 (its patch GOOD); a-3 only r1, the judge undecided; a-4 by none."""
    runs = tmp_path / "runs"
    write_run(runs, "r1", {"acme__a-1": "p1", "acme__a-2": "bad 1", "acme__a-3": "x1", "acme__a-4": ""},
              {"acme__a-1": True, "acme__a-2": False, "acme__a-3": True, "acme__a-4": False})
    write_run(runs, "r2", {"acme__a-1": "p1", "acme__a-2": "GOOD", "acme__a-3": "x2", "acme__a-4": "n"},
              {"acme__a-1": True, "acme__a-2": True, "acme__a-3": False, "acme__a-4": False})
    write_run(runs, "r3", {"acme__a-1": "p1b", "acme__a-2": "bad 1\n", "acme__a-3": "", "acme__a-4": "n"},
              {"acme__a-1": True, "acme__a-2": False, "acme__a-3": False, "acme__a-4": False})
    dataset = tmp_path / "verified.jsonl"
    dataset.write_text("".join(json.dumps({"instance_id": i, "problem_statement": f"issue {i}"}) + "\n" for i in IDS))
    return {"runs": runs, "dataset": dataset, "out": tmp_path / "out"}


def args(chooser, disk, *extra):
    return chooser.parser().parse_args(["--runs", "r1,r2,r3", "--runs-dir", str(disk["runs"]),
                                        "--dataset", str(disk["dataset"]), "--out", str(disk["out"]), *extra])


def test_only_the_tasks_where_the_runs_disagree_are_judged_with_their_distinct_patches(chooser, disk):
    runs = [chooser.load_run(disk["runs"], name) for name in ("r1", "r2", "r3")]
    tasks = chooser.select_tasks(runs)
    assert [t["instance_id"] for t in tasks] == ["acme__a-2", "acme__a-3"]
    # The same text up to trailing whitespace is one candidate; an empty patch is none.
    a2 = tasks[0]["candidates"]
    assert [(c["runs"], c["resolved"]) for c in a2] == [(["r1", "r3"], False), (["r2"], True)]
    assert [c["runs"] for c in tasks[1]["candidates"]] == [["r1"], ["r2"]]
    assert len(chooser.select_tasks(runs, every=True)) == 4


def test_a_dry_run_contacts_no_server_and_writes_nothing(chooser, disk, capsys):
    assert chooser.run_test(args(chooser, disk, "--dry-run")) == 0
    assert "2 task(s) from r1, r2, r3; 4 model call(s) at most" in capsys.readouterr().out
    assert not disk["out"].exists()


def test_the_chooser_judges_both_orders_scores_against_the_baselines_and_resumes(chooser, disk, server, capsys):
    assert chooser.run_test(args(chooser, disk, "--server", server["url"], "--seed", "7")) == 0
    seen = server["seen"]
    assert len(seen) == 4 and all(p["model"] == "fake-model" and p["seed"] == 7 for p in seen)
    assert "issue acme__a-2" in seen[0]["messages"][1]["content"]
    summary = json.loads((disk["out"] / "summary.json").read_text())
    # a-2: GOOD wins both orders (credit 1); a-3: undecided, a tie between a resolving and a failing patch (0.5).
    assert summary["per_task"]["acme__a-2"]["credit"] == 1.0 and summary["per_task"]["acme__a-3"]["credit"] == 0.5
    assert (summary["tasks"], summary["chooser"], summary["random_run"], summary["oracle"]) == (2, 1.5, 0.67, 2)
    assert summary["single_runs"] == {"r1": 2, "r2": 2, "r3": 1} and summary["all_resolved"] == 1
    assert summary["projected"] == 2.5
    assert "chooser 1.5, a random run 0.67, any run (oracle) 2" in capsys.readouterr().out
    # Run again: every pair is already judged, so nothing is asked.
    assert chooser.run_test(args(chooser, disk, "--server", server["url"])) == 0
    assert len(server["seen"]) == 4
    assert len((disk["out"] / "judgments.jsonl").read_text().splitlines()) == 4


def test_a_refusing_server_stops_the_test(chooser, disk, server):
    server["state"]["refuse"] = True
    with pytest.raises(SystemExit, match="answered 503"):
        chooser.run_test(args(chooser, disk, "--server", server["url"]))


def test_the_winner_is_the_last_line_that_names_one(chooser):
    assert chooser.winner("WINNER: A is tempting, but\nWINNER: **B**") == "B"
    assert chooser.winner("no decision") is None
    assert chooser.clip("abcdef", 3).startswith("abc\n[... cut: 3 more")
    assert chooser.Client("http://node.local:8000/v1/").base == "http://node.local:8000/v1"
