"""`scripts/swe_bench_chooser_v2.py`: the hybrid chooser, with docker replaced by scratch git
repositories on the host and the model by a fake server on loopback."""

import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dreamference.swe_bench.swe_bench_docker import SweBenchDocker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "mightling/fake"
IDS = ["acme__calc-1", "acme__calc-2", "acme__calc-3", "acme__calc-4"]
SECRETS = ("SECRET-GOLD", "SECRET-TEST-PATCH", "SECRET-HINT", "SECRET-F2P", "SECRET-P2P")

CALC = "def add(a, b):\n    return a - b\n"


def diff(new_line, marker=""):
    """A patch of calc.py's second line, as an agent's run would have collected it."""
    return ("diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
            f" def add(a, b):\n-    return a - b\n+    {new_line}{marker}\n")


GOOD = diff("return a + b", "  # GOOD")
ALSO_GOOD = diff("return b + a")
WRONG = diff("return a * b")  # right for add(2, 2), wrong for add(2, 3)
NOT_APPLYING = diff("return a + b").replace("return a - b", "return a ^ b")

# The test writer's answers, by the issue they were asked about.
TEST_ADD_2_3 = "```python\nfrom calc import add\nassert add(2, 3) == 5, add(2, 3)\n```"
TEST_ADD_2_2 = "```python\nfrom calc import add\nassert add(2, 2) == 4\n```"
TEST_PASSES = "```python\nfrom calc import add\nassert add(0, 0) == 0\n```"
TEST_BROKEN = "```python\ndef broken(:\n```"
WRITER = {
    "issue acme__calc-1": TEST_ADD_2_3 + "\n" + TEST_ADD_2_2 + "\n" + TEST_PASSES + "\n```bash\nrm -rf /\n```",
    "issue acme__calc-2": TEST_ADD_2_3,
    "issue acme__calc-3": TEST_ADD_2_3,
    "issue acme__calc-4": TEST_PASSES + "\n" + TEST_BROKEN,
}


@pytest.fixture
def v2():
    names = ("swe_bench_chooser_v2", "swe_bench_chooser_test")
    spec = importlib.util.spec_from_file_location(names[0], os.path.join(ROOT, "scripts", "swe_bench_chooser_v2.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[names[0]] = module
    spec.loader.exec_module(module)
    yield module
    for name in names:
        sys.modules.pop(name, None)


@pytest.fixture
def server():
    """Writes the canned tests; as the judge, rates a patch marked GOOD 9 and any other 3."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply({"data": [{"id": "fake-model"}]})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            text = payload["messages"][1]["content"]
            if "<solution_A>" not in text:
                issue = text.split("<issue>")[1].split("</issue>")[0].strip()
                return self.reply({"choices": [{"message": {"content": WRITER[issue]}}]})
            a = text.split("<solution_A>")[1].split("</solution_A>")[0]
            b = text.split("<solution_B>")[1].split("</solution_B>")[0]
            rate = lambda patch: 9 if "GOOD" in patch else 3
            self.reply({"choices": [{"message": {"content": f"RATING_A: {rate(a)}\nRATING_B: {rate(b)}"}}]})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{httpd.server_address[1]}", "seen": seen}
    httpd.shutdown()


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """
    `SweBenchDocker.run` against scratch repositories: `run` copies a template repository (the
    image), `exec -i … bash -s` runs the script on the host against that copy, `rm` deletes it.
    Images start absent, so every task pulls its image and must remove it.
    """
    template = tmp_path / "image" / "testbed"
    template.mkdir(parents=True)
    (template / "calc.py").write_text(CALC)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(git + ["init", "-q"], cwd=template, check=True)
    subprocess.run(git + ["add", "-A"], cwd=template, check=True)
    subprocess.run(git + ["commit", "-qm", "base"], cwd=template, check=True)
    state = {"calls": [], "present": set(), "containers": {}, "removed_images": [], "scripts": [],
             "exec_hook": None}
    python_dir = os.path.dirname(sys.executable)

    def run(args, timeout=None, input_text=None):
        state["calls"].append(list(args))
        done = lambda code=0, out="", err="": subprocess.CompletedProcess(["docker", *args], code, out, err)
        verb = args[0]
        if verb == "image" and args[1] == "inspect":
            return done(0, '[] sha256:x') if args[2] in state["present"] else done(1)
        if verb == "pull":
            state["present"].add(args[-1])
            return done()
        if verb == "rmi":
            state["removed_images"].append(args[1])
            state["present"].discard(args[1])
            return done()
        if verb == "ps":
            return done(0, "")
        if verb == "rm":
            shutil.rmtree(state["containers"].pop(args[-1], tmp_path / "none"), ignore_errors=True)
            return done()
        if verb == "run":
            name = args[args.index("--name") + 1]
            root = tmp_path / "containers" / name
            shutil.copytree(template.parent, root)
            state["containers"][name] = root
            return done(0, "id\n")
        if verb == "exec":
            name = args[2]
            state["scripts"].append(input_text)
            if state["exec_hook"]:
                hooked = state["exec_hook"](args)
                if hooked is not None:
                    return hooked
            root = state["containers"][name]
            env = dict(os.environ, TESTBED=str(root / "testbed"), WORK=str(root / "work"),
                       PATH=python_dir + os.pathsep + os.environ.get("PATH", ""))
            return subprocess.run(["bash", "-s"], input=input_text, capture_output=True, text=True, env=env,
                                  cwd=root, timeout=120)
        raise AssertionError(f"unexpected docker call: {args}")

    monkeypatch.setattr(SweBenchDocker, "run", staticmethod(run))
    return state


def write_run(root, name, patches, results, reports=None):
    directory = root / name
    (directory / "eval" / "1").mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({
        "name": name, "instances": IDS, "model_name_or_path": MODEL,
        "images": {i: {"image": f"example/arm64:{i}"} for i in IDS}}))
    (directory / "predictions.jsonl").write_text("".join(
        json.dumps({"instance_id": i, "model_name_or_path": MODEL, "model_patch": patches[i]}) + "\n" for i in IDS))
    (directory / "eval" / "1" / "grading.json").write_text(json.dumps({"grader": {}, "results": results}))


def verdict(resolved, p2p_failed=0, applied=True):
    return {"resolved": resolved, "applied": applied, "f2p": 1, "f2p_failed": 0 if resolved else 1,
            "p2p": 4, "p2p_failed": p2p_failed}


@pytest.fixture
def disk(tmp_path):
    """
    calc-1: GOOD (r1, r3; resolved) against WRONG (r2): the reproduction tests decide.
    calc-2: GOOD (r1, resolved) and ALSO_GOOD (r2, not resolved here, to tell them apart) behave
            alike; r3's WRONG breaks a PASS_TO_PASS test: the filter, the groups tie, the judge.
    calc-3: r2's patch breaks a PASS_TO_PASS test, r1's resolves, r3's does not apply: the filter.
    calc-4: every test the writer gives passes or does not compile: the judge, without tests.
    """
    runs = tmp_path / "runs"
    write_run(runs, "r1", {"acme__calc-1": GOOD, "acme__calc-2": GOOD, "acme__calc-3": GOOD, "acme__calc-4": GOOD},
              {"acme__calc-1": verdict(True), "acme__calc-2": verdict(True), "acme__calc-3": verdict(True),
               "acme__calc-4": verdict(True)})
    write_run(runs, "r2", {"acme__calc-1": WRONG, "acme__calc-2": ALSO_GOOD, "acme__calc-3": WRONG,
                           "acme__calc-4": WRONG},
              {"acme__calc-1": verdict(False), "acme__calc-2": verdict(False), "acme__calc-3": verdict(False, 2),
               "acme__calc-4": verdict(False)})
    write_run(runs, "r3", {"acme__calc-1": GOOD, "acme__calc-2": WRONG, "acme__calc-3": NOT_APPLYING,
                           "acme__calc-4": ALSO_GOOD},
              {"acme__calc-1": verdict(True), "acme__calc-2": verdict(False, 1), "acme__calc-3": verdict(False, applied=False),
               "acme__calc-4": verdict(False)})
    dataset = tmp_path / "verified.jsonl"
    eval_script = ("#!/bin/bash\nset -uxo pipefail\necho setting-up\ngit status\ngit show\n"
                   "git -c core.fileMode=false diff base0\ngit checkout base0 tests/test_calc.py\n"
                   "git apply -v - <<'EOF'\nSECRET-TEST-PATCH\nEOF\n")
    dataset.write_text("".join(json.dumps({
        "instance_id": i, "problem_statement": f"issue {i}", "repo": "acme/calc", "version": "1.0",
        "base_commit": "base0", "eval_script": eval_script, "patch": "SECRET-GOLD", "test_patch": "SECRET-TEST-PATCH",
        "hints_text": "SECRET-HINT", "FAIL_TO_PASS": "SECRET-F2P", "PASS_TO_PASS": "SECRET-P2P"}) + "\n" for i in IDS))
    v1_summary = tmp_path / "v1-summary.json"
    v1_summary.write_text(json.dumps({"runs": ["r1", "r2", "r3"], "chooser": 2.0, "projected": 2.0, "per_task": {
        i: {"stage": "judge", "chooser": 0.0 if i == "acme__calc-1" else 1.0} for i in IDS}}))
    return {"runs": runs, "dataset": dataset, "out": tmp_path / "out", "v1": v1_summary}


def args(v2, disk, *extra):
    return v2.parser().parse_args(["--runs", "r1,r2,r3", "--runs-dir", str(disk["runs"]), "--dataset", str(disk["dataset"]),
                                   "--out", str(disk["out"]), "--v1-summary", str(disk["v1"]), *extra])


# --------------------------------------------------------------------------- the whole test


def test_the_four_stages_in_containers_scored_beside_v1_and_resumed(v2, disk, docker, server, capsys):
    assert v2.run_test(args(v2, disk, "--server", server["url"], "--seed", "7")) == 0
    summary = json.loads((disk["out"] / "summary.json").read_text())
    per_task = summary["per_task"]
    assert {i: per_task[i]["stage"] for i in IDS} == {
        "acme__calc-1": "reproduction tests", "acme__calc-2": "judge",
        "acme__calc-3": "regression filter", "acme__calc-4": "judge (no repro test)"}
    assert all(per_task[i]["chooser"] == 1.0 for i in IDS)
    assert summary["chooser"] == 4.0 and summary["v1"]["chooser"] == 3.0
    assert summary["stages_v2"]["reproduction tests"] == {"tasks": 1, "right": 1.0}
    assert {row["instance_id"]: row["v1_stage"] for row in summary["side_by_side"]}["acme__calc-1"] == "judge"

    # calc-1: two tests kept (2+3 and 2+2 fail on a - b), the one passing there dropped, the bash block no test.
    record = json.loads((disk["out"] / "tasks" / "acme__calc-1.json").read_text())
    assert record["kept"] == [0, 1] and len(record["tests"]) == 3
    assert per_task["acme__calc-1"]["vectors"] == {v2.v1.digest(GOOD): [1, 1], v2.v1.digest(WRONG): [0, 1]}
    # calc-2: the filter drops WRONG; GOOD and ALSO_GOOD pass alike, so the judge: one pair, three calls.
    pair = per_task["acme__calc-2"]["pairs"][0]
    assert [(c["a"], c["b"]) for c in pair["calls"]][0] == [(c["a"], c["b"]) for c in pair["calls"]][2]
    assert pair["calls"][0]["a"] == pair["calls"][1]["b"] and pair["winner"] == v2.v1.digest(GOOD)
    judge_calls = [p for p in server["seen"] if "<solution_A>" in p["messages"][1]["content"]]
    assert len(judge_calls) == 12  # calc-2: one pair; calc-4: three survivors, three pairs
    assert sorted(p["seed"] for p in judge_calls) == [7] * 4 + [8] * 4 + [9] * 4
    assert "<test_results_A>" in judge_calls[0]["messages"][1]["content"]
    # calc-4: two requests to the writer (nothing kept the first time), the second told so.
    writer_calls = [p for p in server["seen"] if "<solution_A>" not in p["messages"][1]["content"]]
    calc4 = [p for p in writer_calls if "issue acme__calc-4" in p["messages"][1]["content"]]
    assert len(calc4) == 2 and "none of them failed" in calc4[1]["messages"][1]["content"]
    assert [p["seed"] for p in calc4] == [7, 1007]
    # calc-3: a patch that does not apply is recorded as such.
    record3 = json.loads((disk["out"] / "tasks" / "acme__calc-3.json").read_text())
    assert record3["candidates"][v2.v1.digest(NOT_APPLYING)]["applied"] is None
    assert record3["candidates"][v2.v1.digest(GOOD)]["applied"] == "git apply --verbose"

    # Nothing the chooser may not see reached the model or a container; the set-up did.
    sent = json.dumps(server["seen"]) + "".join(docker["scripts"])
    assert not [s for s in SECRETS if s in sent]
    assert all("echo setting-up" in s and "git status" not in s for s in docker["scripts"])
    # Every container removed, no network, capped; every image this test pulled removed.
    assert docker["containers"] == {}
    starts = [c for c in docker["calls"] if c[0] == "run"]
    assert all(c[c.index("--network") + 1] == "none" and "--memory" in c for c in starts)
    assert sorted(docker["removed_images"]) == sorted(f"example/arm64:{i}" for i in IDS)
    out = capsys.readouterr().out
    assert "v1 3.0, v2 4.0" in out and "reproduction tests 1.0/1" in out

    # Run again: nothing is asked and no container starts.
    asked, started = len(server["seen"]), len(starts)
    assert v2.run_test(args(v2, disk, "--server", server["url"], "--seed", "7")) == 0
    assert len(server["seen"]) == asked
    assert len([c for c in docker["calls"] if c[0] == "run"]) == started


def test_the_time_cap_ends_a_task_cleans_up_and_falls_back_to_the_survivors(v2, disk, docker, server, monkeypatch):
    clock = {"now": 0.0}

    class Deadline(v2.Deadline):
        def __init__(self, seconds):
            super().__init__(seconds, clock=lambda: clock["now"])

    def hang(args):
        clock["now"] += 10_000  # the exec ran past the task's cap
        return subprocess.CompletedProcess(args, 124, "", "timed out")

    monkeypatch.setattr(v2, "Deadline", Deadline)
    docker["exec_hook"] = hang
    assert v2.run_test(args(v2, disk, "--server", server["url"], "--only", "acme__calc-2,acme__calc-3")) == 0
    summary = json.loads((disk["out"] / "summary.json").read_text())
    # calc-2 had two survivors: a fair coin between them. calc-3: the filter still decides.
    assert summary["per_task"]["acme__calc-2"]["stage"] == "time cap"
    assert summary["per_task"]["acme__calc-2"]["chooser"] == 0.5
    assert summary["per_task"]["acme__calc-3"]["stage"] == "regression filter"
    assert summary["timed_out"] == ["acme__calc-2", "acme__calc-3"]
    assert docker["containers"] == {} and len(docker["removed_images"]) == 2


def test_a_dry_run_contacts_no_server_and_starts_no_container(v2, disk, docker, capsys):
    assert v2.run_test(args(v2, disk, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "4 task(s) from r1, r2, r3; at most 8 test-writer call(s) and 15 judge call(s)" in out
    assert "[to pull]" in out
    assert {c[0] for c in docker["calls"]} == {"image"} and not disk["out"].exists()


def test_the_fake_model_needs_no_server(v2, disk, docker):
    assert v2.run_test(args(v2, disk, "--fake-model", "--only", "acme__calc-2")) == 0
    summary = json.loads((disk["out"] / "summary.json").read_text())
    assert summary["server"] == "fake" and summary["per_task"]["acme__calc-2"]["kept_tests"] == 2


# --------------------------------------------------------------------------- the parts


def candidate(v2, cid, runs=1, broken=False):
    return v2.Candidate(id=cid, patch=f"patch {cid}", runs=[f"r{n}" for n in range(runs)], p2p_failed=1 if broken else 0)


def test_the_stages_in_order(v2):
    never = lambda a, b, q: pytest.fail("the judge was called")
    a, b, c = candidate(v2, "a"), candidate(v2, "b"), candidate(v2, "c")
    # Most reproduction tests passed decides.
    choice = v2.decide([a, b, c], {"a": [True, True], "b": [True, False], "c": [False, False]}, never, random.Random(0))
    assert (choice["stage"], choice["chosen"]) == ("reproduction tests", ["a"])
    # The filter comes first: a passes most, but breaks an existing test.
    broken_a = candidate(v2, "a", broken=True)
    choice = v2.decide([broken_a, b, c], {"a": [True, True], "b": [True, False], "c": [False, False]}, never, random.Random(0))
    assert (choice["stage"], choice["chosen"]) == ("reproduction tests", ["b"]) and choice["survivors"] == ["b", "c"]
    # Two groups pass one test each: the larger, counted in runs, wins.
    twice = candidate(v2, "a", runs=2)
    choice = v2.decide([twice, b], {"a": [True, False], "b": [False, True]}, never, random.Random(0))
    assert (choice["stage"], choice["chosen"]) == ("behaviour groups", ["a"])
    # The filter alone.
    choice = v2.decide([a, candidate(v2, "b", broken=True)], None, never, random.Random(0))
    assert (choice["stage"], choice["chosen"]) == ("regression filter", ["a"])
    assert v2.decide([a], None, never, random.Random(0))["stage"] == "only candidate"
    assert v2.decide([], None, never, random.Random(0))["stage"] == "none"
    # Out of time before the tests ran: a fair coin among the survivors.
    choice = v2.decide([a, b], None, never, random.Random(0), timed_out=True)
    assert (choice["stage"], choice["chosen"]) == ("time cap", ["a", "b"])


def test_the_judge_only_when_ties_remain_and_with_or_without_tests(v2):
    a, b, c = candidate(v2, "a"), candidate(v2, "b"), candidate(v2, "c")
    asked = []

    def ask(x, y, query):
        asked.append((x.id, y.id, query))
        return (9, 3) if x.id == "b" else (3, 9) if y.id == "b" else (5, 5)

    # One group of two distinct patches (c passes fewer): the judge between a and b only.
    choice = v2.decide([a, b, c], {"a": [True], "b": [True], "c": [False]}, ask, random.Random(0))
    assert (choice["stage"], choice["chosen"], choice["pool"]) == ("judge", ["b"], ["a", "b"])
    assert len(asked) == 3 and {q for _, _, q in asked} == {0, 1, 2}
    # No test kept: every candidate goes to the judge, and the stage says so.
    asked.clear()
    choice = v2.decide([a, b, c], {"a": [], "b": [], "c": []}, ask, random.Random(0))
    assert (choice["stage"], choice["chosen"]) == ("judge (no repro test)", ["b"]) and len(asked) == 9

    def late(x, y, query):
        raise v2.TaskTimeout()

    choice = v2.decide([a, b], {"a": [True], "b": [True]}, late, random.Random(0))
    assert (choice["stage"], choice["chosen"]) == ("time cap", ["a", "b"])


def test_three_calls_with_the_order_swapped_and_the_majority_wins(v2):
    a, b = candidate(v2, "a"), candidate(v2, "b")
    # A judge that always prefers whatever is shown first: the first-shown wins 2-1, which the
    # swap makes visible as a split, not a 3-0.
    pair = v2.judge_pair(a, b, lambda x, y, q: (9, 3), random.Random(0))
    orders = [(c["a"], c["b"]) for c in pair["calls"]]
    assert orders[0] == orders[2] == orders[1][::-1]
    assert sorted(pair["votes"].values()) == [1, 2] and pair["winner"] == orders[0][0]
    # A consistent judge wins 3-0; ties and unreadable answers vote for nobody.
    pair = v2.judge_pair(a, b, lambda x, y, q: (9, 3) if x.id == "b" else (3, 9), random.Random(0))
    assert pair["votes"] == {"a": 0, "b": 3} and pair["winner"] == "b"
    pair = v2.judge_pair(a, b, lambda x, y, q: None if q else (5, 5), random.Random(0))
    assert pair["votes"] == {"a": 0, "b": 0} and pair["winner"] is None


def test_the_test_writer_is_given_the_issue_only(v2, disk):
    rows = v2.load_rows(disk["dataset"], ["acme__calc-1"])
    row = rows["acme__calc-1"]
    assert not [s for s in SECRETS if s in json.dumps(row)], "the hidden fields are dropped on reading"
    assert "eval_script" not in row and row["setup"] == "echo setting-up"
    payload = v2.writer_payload(row, "m", v2.parser().parse_args(["--seed", "5"]), 1, "feedback here")
    text = json.dumps(payload)
    assert "issue acme__calc-1" in text and "acme/calc (version 1.0)" in text and "feedback here" in text
    assert payload["seed"] == 1005 and "setting-up" not in text


def test_the_set_up_is_the_graders_up_to_the_hidden_tests(v2):
    row = {"base_commit": "abc", "eval_script": "#!/bin/bash\nset -uxo pipefail\nsource /opt/miniconda3/bin/activate\n"
           "conda activate testbed\ngit status\ngit -c core.fileMode=false diff abc\npython -m pip install -e .\n"
           "git checkout abc tests/a.py\ngit apply -v - <<'EOF'\nhidden\nEOF\n"}
    assert v2.setup_preamble(row) == ("source /opt/miniconda3/bin/activate\nconda activate testbed\n"
                                      "python -m pip install -e .")
    assert v2.setup_preamble({}) == "source /opt/miniconda3/bin/activate\nconda activate testbed"


def test_tests_and_container_output_are_read(v2):
    answer = ("```python\nprint(1)\n```\n```bash\necho no\n```\n```py\nprint(2)\n```\n"
              "```\nprint(3)\n```\n```python\nprint(4)\n```")
    assert v2.parse_tests(answer) == ["print(1)\n", "print(2)\n", "print(3)\n"]
    stdout = ("@@CHOOSER apply git apply --verbose\n@@CHOOSER setup 0\n@@CHOOSER test 1 compile 0\n"
              "@@CHOOSER test 1 output\nTraceback\nAssertionError\n@@CHOOSER test 1 exit 1\n"
              "@@CHOOSER test 2 compile 1\n@@CHOOSER test 2 output\n\n@@CHOOSER test 2 exit 0\n")
    result = v2.parse_container(stdout, 2)
    assert result["applied"] == "git apply --verbose" and result["setup"] == 0
    assert result["tests"][0] == {"compiled": True, "exit": 1, "output": "Traceback\nAssertionError"}
    assert result["tests"][1]["compiled"] is False
    assert v2.parse_container("@@CHOOSER apply failed\nerror: patch failed\n", 1)["applied"] is None
    assert v2.vector_of({"applied": None, "tests": [{"exit": 0}]}, [0]) == [False]
