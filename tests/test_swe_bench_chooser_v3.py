"""`scripts/swe_bench_chooser_v3.py`: chooser v1 with a differential search, with docker replaced by
scratch git repositories on the host, the model by a fake server on loopback, and the night check
by a stand-in (no test asks the real systemd or reaches the model server's port)."""

import importlib.util
import json
import os
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

SEPARATES = ("The fixes differ on add(2, 3).\nEXPECTED: add(2, 3) == 5\n"
             "```python\nimport sys\nfrom calc import add\nprint(add(2, 3))\nsys.exit(0 if add(2, 3) == 5 else 1)\n```")
PRINTS_ONLY = ("They differ on add(2, 3).\nEXPECTED: 5\n"
               "```python\nfrom calc import add\nprint(add(2, 3))\nraise SystemExit(1)\n```")
WRITER = {"issue acme__calc-1": SEPARATES, "issue acme__calc-2": SEPARATES, "issue acme__calc-3": SEPARATES,
          "issue acme__calc-4": PRINTS_ONLY}


@pytest.fixture
def v3():
    names = ("swe_bench_chooser_v3", "swe_bench_chooser_v2", "swe_bench_chooser_test")
    spec = importlib.util.spec_from_file_location(names[0], os.path.join(ROOT, "scripts", "swe_bench_chooser_v3.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[names[0]] = module
    spec.loader.exec_module(module)
    yield module
    for name in names:
        sys.modules.pop(name, None)


@pytest.fixture
def server(v3):
    """The writer's canned scripts; the verdict picks the output that printed 5; v1's judge rates GOOD 9."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, text):
            data = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            data = json.dumps({"data": [{"id": "fake-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            system, text = payload["messages"][0]["content"], payload["messages"][1]["content"]
            if system == v3.WRITER_SYSTEM:
                return self.reply(WRITER[text.split("<issue>")[1].split("</issue>")[0].strip()])
            if system == v3.VERDICT_SYSTEM:
                p = text.split("<output_P")[1].split("</output_P>")[0]
                return self.reply("BEHAVIOUR: P" if "\n5\n" in p else "BEHAVIOUR: Q")
            a = text.split("<solution_A>")[1].split("</solution_A>")[0]
            b = text.split("<solution_B>")[1].split("</solution_B>")[0]
            rate = lambda patch: 9 if "GOOD" in patch else 3
            self.reply(f"RATING_A: {rate(a)}\nRATING_B: {rate(b)}")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{httpd.server_address[1]}", "seen": seen}
    httpd.shutdown()


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """`SweBenchDocker.run` against scratch repositories (as in the version 2 tests); images start absent."""
    template = tmp_path / "image" / "testbed"
    template.mkdir(parents=True)
    (template / "calc.py").write_text(CALC)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(git + ["init", "-q"], cwd=template, check=True)
    subprocess.run(git + ["add", "-A"], cwd=template, check=True)
    subprocess.run(git + ["commit", "-qm", "base"], cwd=template, check=True)
    state = {"calls": [], "present": set(), "containers": {}, "removed_images": [], "scripts": []}
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
            root = state["containers"][args[2]]
            state["scripts"].append(input_text)
            env = dict(os.environ, TESTBED=str(root / "testbed"), WORK=str(root / "work"),
                       PATH=python_dir + os.pathsep + os.environ.get("PATH", ""))
            return subprocess.run(["bash", "-s"], input=input_text, capture_output=True, text=True, env=env,
                                  cwd=root, timeout=120)
        raise AssertionError(f"unexpected docker call: {args}")

    monkeypatch.setattr(SweBenchDocker, "run", staticmethod(run))
    return state


def write_run(root, name, patches, results):
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
    calc-1: GOOD (r1, r3; resolved) against WRONG (r2): the script's expectation decides.
    calc-2: GOOD (r1, resolved) and ALSO_GOOD (r2, not resolved here, to tell them apart) behave
            alike; r3's WRONG breaks a PASS_TO_PASS test: every round is void, v1's own answer decides.
    calc-3: r2's patch breaks a PASS_TO_PASS test, r1's resolves, r3's does not apply: the filter.
    calc-4: GOOD (r1) against WRONG (r2, r3); the script fails on both and prints 5 and 6: the verdict.
    """
    runs = tmp_path / "runs"
    write_run(runs, "r1", {"acme__calc-1": GOOD, "acme__calc-2": GOOD, "acme__calc-3": GOOD, "acme__calc-4": GOOD},
              {i: verdict(True) for i in IDS})
    write_run(runs, "r2", {"acme__calc-1": WRONG, "acme__calc-2": ALSO_GOOD, "acme__calc-3": WRONG, "acme__calc-4": WRONG},
              {"acme__calc-1": verdict(False), "acme__calc-2": verdict(False), "acme__calc-3": verdict(False, 2),
               "acme__calc-4": verdict(False)})
    write_run(runs, "r3", {"acme__calc-1": GOOD, "acme__calc-2": WRONG, "acme__calc-3": NOT_APPLYING, "acme__calc-4": WRONG},
              {"acme__calc-1": verdict(True), "acme__calc-2": verdict(False, 1),
               "acme__calc-3": verdict(False, applied=False), "acme__calc-4": verdict(False)})
    dataset = tmp_path / "verified.jsonl"
    eval_script = ("#!/bin/bash\nset -uxo pipefail\necho setting-up\ngit status\n"
                   "git checkout base0 tests/test_calc.py\ngit apply -v - <<'EOF'\nSECRET-TEST-PATCH\nEOF\n")
    dataset.write_text("".join(json.dumps({
        "instance_id": i, "problem_statement": f"issue {i}", "repo": "acme/calc", "version": "1.0",
        "base_commit": "base0", "eval_script": eval_script, "patch": "SECRET-GOLD", "test_patch": "SECRET-TEST-PATCH",
        "hints_text": "SECRET-HINT", "FAIL_TO_PASS": "SECRET-F2P", "PASS_TO_PASS": "SECRET-P2P"}) + "\n" for i in IDS))
    v1_dir = tmp_path / "v1"
    v1_dir.mkdir()
    (v1_dir / "summary.json").write_text(json.dumps({"runs": ["r1", "r2", "r3"], "chooser": 3.0, "projected": 3.0, "per_task": {
        i: {"stage": "judge", "chooser": 0.0 if i == "acme__calc-1" else 1.0} for i in IDS}}))
    return {"runs": runs, "dataset": dataset, "out": tmp_path / "out", "v1": v1_dir / "summary.json", "v1_dir": v1_dir}


def write_v1_judgments(v3, disk):
    """Version 1's answer on calc-2's pair (both orders: the tournament's order is its own): GOOD wins."""
    good, also = v3.v1.digest(GOOD), v3.v1.digest(ALSO_GOOD)
    lines = [{"instance_id": "acme__calc-2", "a": good, "b": also, "ratings": [8, 4]},
             {"instance_id": "acme__calc-2", "a": also, "b": good, "ratings": [4, 8]}]
    (disk["v1_dir"] / "judgments.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))


def args(v3, disk, *extra):
    return v3.parser().parse_args(["--runs", "r1,r2,r3", "--runs-dir", str(disk["runs"]), "--dataset", str(disk["dataset"]),
                                   "--out", str(disk["out"]), "--v1-summary", str(disk["v1"]), *extra])


def no_night():
    return []


def test_the_search_decides_pairs_from_behaviour_and_leaves_the_rest_to_v1s_answer(v3, disk, docker, server, capsys):
    write_v1_judgments(v3, disk)
    assert v3.run_test(args(v3, disk, "--server", server["url"], "--seed", "7"), night_check=no_night) == 0
    summary = json.loads((disk["out"] / "summary.json").read_text())
    per_task = summary["per_task"]
    assert {i: per_task[i]["stage_v3"] for i in IDS} == {
        "acme__calc-1": "differential", "acme__calc-2": "diff judge",
        "acme__calc-3": "regression filter", "acme__calc-4": "differential"}
    assert all(per_task[i]["chooser"] == 1.0 for i in IDS)
    assert summary["chooser"] == 4.0 and summary["v1"]["chooser"] == 3.0
    assert per_task["acme__calc-1"]["pairs"][0]["decided_by"] == "expectation"
    assert per_task["acme__calc-4"]["pairs"][0]["decided_by"] == "outputs verdict"
    assert summary["pairs_by"] == {"expectation": 1, "outputs verdict": 1, "diff judge": 1}
    # calc-2: three void rounds (both meet the expectation), then version 1's own answer, never re-asked.
    pair = per_task["acme__calc-2"]["pairs"][0]
    assert [r["void"] for r in pair["rounds"]] == ["both met the expectation"] * 3
    assert summary["v1_reused"] == 1
    assert not [p for p in server["seen"] if p["messages"][0]["content"] == v3.NightShiftChooser.messages("", "", "")[0]["content"]]
    writes = [p for p in server["seen"] if p["messages"][0]["content"] == v3.WRITER_SYSTEM]
    calc2 = [p for p in writes if "issue acme__calc-2" in p["messages"][1]["content"]]
    assert len(calc2) == 3 and "did not separate" not in calc2[0]["messages"][1]["content"]
    assert "did not separate" in calc2[1]["messages"][1]["content"] and "fix X exit 0" in calc2[2]["messages"][1]["content"]
    assert [p["seed"] for p in calc2] == [7, 107, 207]
    # Pair accuracy: every pair differs in the verdict; calc-1's and calc-4's decided right by the search.
    accuracy = summary["pair_accuracy"]
    assert (accuracy["pairs"], accuracy["differential"], accuracy["differential_right"], accuracy["v3_right"]) == (3, 2, 2, 3)
    # The verdict call saw outputs, never a diff.
    verdicts = [p for p in server["seen"] if p["messages"][0]["content"] == v3.VERDICT_SYSTEM]
    assert len(verdicts) == 1 and "diff --git" not in verdicts[0]["messages"][1]["content"]
    # Nothing the chooser may not see reached the model or a container; the set-up did.
    sent = json.dumps(server["seen"]) + "".join(docker["scripts"])
    assert not [s for s in SECRETS if s in sent]
    assert all("echo setting-up" in s for s in docker["scripts"])
    # Containers removed, no network, capped; v3's own label; calc-3 (the filter) needed no image.
    assert docker["containers"] == {}
    starts = [c for c in docker["calls"] if c[0] == "run"]
    assert all(c[c.index("--network") + 1] == "none" and "--memory" in c for c in starts)
    assert all(c[c.index("--label") + 1] == v3.CONTAINER_LABEL for c in starts)
    assert sorted(docker["removed_images"]) == sorted(f"example/arm64:{i}" for i in IDS if i != "acme__calc-3")
    out = capsys.readouterr().out
    assert "v1 3.0, v3 4.0" in out

    # Run again: nothing is asked and no container starts.
    asked, started = len(server["seen"]), len(starts)
    assert v3.run_test(args(v3, disk, "--server", server["url"], "--seed", "7"), night_check=no_night) == 0
    assert len(server["seen"]) == asked and len([c for c in docker["calls"] if c[0] == "run"]) == started

    # The verdict over this "trio": better (calc-1 gained, nothing lost).
    out_file = disk["out"] / "verdict.json"
    assert v3.run_test(v3.parser().parse_args(["--verdict", str(disk["out"] / "summary.json"),
                                               "--verdict-out", str(out_file)])) == 0
    result = json.loads(out_file.read_text())
    assert result["go"] and result["better"] and result["gained"] == ["acme__calc-1"] and result["lost"] == []


def test_without_v1s_answer_the_diff_judge_is_asked_as_v1_asks(v3, disk, docker, server):
    assert v3.run_test(args(v3, disk, "--server", server["url"], "--seed", "7", "--only", "acme__calc-2",
                            "--v1-judgments", str(disk["v1_dir"] / "none.jsonl")), night_check=no_night) == 0
    v1_system = v3.NightShiftChooser.messages("", "", "")[0]["content"]
    asked = [p for p in server["seen"] if p["messages"][0]["content"] == v1_system]
    assert len(asked) == 1 and asked[0]["seed"] == 7
    summary = json.loads((disk["out"] / "summary.json").read_text())
    assert summary["per_task"]["acme__calc-2"]["chosen"] == [v3.v1.digest(GOOD)]


def test_a_night_pauses_the_test_and_the_same_command_resumes_it(v3, disk, docker, server):
    write_v1_judgments(v3, disk)
    checks = {"n": 0}

    def night_after_a_while():
        checks["n"] += 1
        return ["ling-swe-night13.service"] if checks["n"] > 6 else []

    assert v3.run_test(args(v3, disk, "--server", server["url"], "--seed", "7"), night_check=night_after_a_while) == v3.PAUSED
    assert not (disk["out"] / "summary.json").exists()
    asked = len(server["seen"])
    assert asked > 0
    # A night already active: nothing is asked, nothing runs.
    started = len([c for c in docker["calls"] if c[0] == "run"])
    assert v3.run_test(args(v3, disk, "--server", server["url"], "--seed", "7"),
                       night_check=lambda: ["ling-swe-night13.service"]) == v3.PAUSED
    assert len(server["seen"]) == asked and len([c for c in docker["calls"] if c[0] == "run"]) == started
    # Resumed: the answers already given are not asked again.
    assert v3.run_test(args(v3, disk, "--server", server["url"], "--seed", "7"), night_check=no_night) == 0
    keys = [json.loads(line)["key"] for line in (disk["out"] / "calls.jsonl").read_text().splitlines()]
    assert len(keys) == len(set(keys)) == len(server["seen"])
    assert json.loads((disk["out"] / "summary.json").read_text())["chooser"] == 4.0


def test_a_dry_run_contacts_no_server_and_starts_no_container(v3, disk, docker, capsys):
    assert v3.run_test(args(v3, disk, "--dry-run"), night_check=no_night) == 0
    out = capsys.readouterr().out
    assert "4 task(s) from r1, r2, r3; 3 go to the comparison, 3 pair(s)" in out and "[to pull]" in out
    assert {c[0] for c in docker["calls"]} == {"image"} and not disk["out"].exists()


def test_the_verdict_counts_a_lost_task_against_v3(v3, tmp_path):
    def summary(name, v1_total, v3_total, rows):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"runs": [name], "tasks": len(rows), "oracle": len(rows), "chooser": v3_total,
                                    "v1": {"chooser": v1_total},
                                    "side_by_side": [{"instance_id": i, "v1": a, "v3": b} for i, a, b in rows]}))
        return str(path)

    tied = summary("t1", 2.0, 2.0, [("x", 1.0, 0.0), ("y", 0.0, 1.0)])
    even = summary("t2", 1.0, 1.0, [("z", 1.0, 1.0)])
    assert v3.verdict([tied, even], tmp_path / "v.json") == 0
    result = json.loads((tmp_path / "v.json").read_text())
    assert result["lost"] == ["x"] and result["gained"] == ["y"] and not result["go"] and not result["better"]
    assert v3.verdict([even], tmp_path / "v.json") == 0
    result = json.loads((tmp_path / "v.json").read_text())
    assert result["go"] and not result["better"]


def test_scripts_and_outputs_are_read(v3):
    expected, script = v3.parse_script("thinking\nEXPECTED: first\n**EXPECTED:** 5 is returned\n"
                                       "```python\nprint(1)\n```\n```python\nprint(2)\n```")
    assert expected == "5 is returned" and script == "print(1)\n"
    assert v3.parse_script("no code here") == ("", None)
    assert v3.normalised("<obj at 0x7f00ab>  /tmp/tmpa1b2/x 0.25 s") == v3.normalised("<obj at 0x1234> /tmp/tmpzz/x 1.50 s")
    assert v3.normalised('File "/testbed/a.py", line 576, in f') == v3.normalised('File "/testbed/a.py", line 580, in f')
    assert v3.outcome({"applied": None}) == (None, "(the patch did not apply)")
    long = "head\n" + "x" * 5000 + "\nValueError: the reason"
    assert v3.ends(long).startswith("head") and v3.ends(long).endswith("ValueError: the reason")
    assert len(v3.ends(long)) < 1000 and v3.ends("short") == "short"
    assert v3.met({"applied": "git apply", "tests": [{"exit": 0, "output": ""}]})
    assert not v3.met({"applied": "git apply", "error": "docker run failed", "tests": [{"exit": 0}]})
