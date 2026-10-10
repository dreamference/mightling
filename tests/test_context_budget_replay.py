"""`scripts/context_budget_replay.py compactions`: the replay count of the restart trigger (survey §3.7)."""

import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("context_budget_replay", os.path.join(ROOT, "scripts", "context_budget_replay.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def a_run(tmp_path, compactions, verdicts):
    run = tmp_path / "run"
    (run / "instances").mkdir(parents=True)
    for instance_id, count in compactions.items():
        (run / "instances" / f"{instance_id}.json").write_text(json.dumps({"instance_id": instance_id, "status": "done"}))
        rollout = run / "scratch" / instance_id / "codex-home" / "sessions" / "2026" / "10" / "02" / "rollout-x.jsonl"
        rollout.parent.mkdir(parents=True)
        lines = [json.dumps({"type": "response_item", "payload": {"type": "message"}})]
        lines += [json.dumps({"type": "compacted", "payload": {}})] * count
        lines.append('{"type": "event_msg", "payload": {"type": "task_complete", "compacted": "not a record"}}')
        rollout.write_text("".join(line + "\n" for line in lines))
    (run / "eval" / "1").mkdir(parents=True)
    (run / "eval" / "1" / "grading.json").write_text(json.dumps({"results": {"stale": {"resolved": True}}}))
    (run / "eval" / "2").mkdir(parents=True)
    (run / "eval" / "2" / "grading.json").write_text(json.dumps({"results": {i: {"resolved": v} for i, v in verdicts.items()}}))
    return str(run)


def test_compactions_are_counted_per_instance_against_the_latest_grading(tmp_path, capsys):
    module = load()
    run = a_run(tmp_path, {"a__1": 0, "b__2": 2, "c__3": 3, "d__4": 7}, {"a__1": True, "b__2": False, "c__3": True})
    assert module.instance_compactions(run) == {"a__1": 0, "b__2": 2, "c__3": 3, "d__4": 7}
    assert module.grading_of(run) == {"a__1": True, "b__2": False, "c__3": True}  # the latest grading, not the first
    module.compactions(run)
    out = capsys.readouterr().out
    assert "4 instance(s) with a state, 3 graded" in out
    assert "restart after 2: 3 task(s) would have restarted (1 resolved, 1 failed, 1 ungraded)" in out
    assert "restart after 4: 1 task(s) would have restarted (0 resolved, 0 failed, 1 ungraded)" in out
    assert "most compacted: d__4 (7), c__3 (3), b__2 (2)" in out


def test_the_command_line_runs_it(tmp_path, capsys, monkeypatch):
    module = load()
    run = a_run(tmp_path, {"a__1": 1}, {"a__1": True})
    monkeypatch.setattr(module.sys, "argv", ["context_budget_replay.py", "compactions", run + "/"])
    assert module.main() == 0
    assert "restart after 1: 1 task(s) would have restarted (1 resolved, 0 failed, 0 ungraded)" in capsys.readouterr().out
