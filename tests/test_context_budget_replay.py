"""`scripts/context_budget_replay.py compactions`: the replay count of the restart trigger (survey §3.7)."""

import importlib.util
import json
import os
import re
from pathlib import Path

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


def a_session(tmp_path):
    """A run with one rollout: a test run, a file read, an index call and a short listing."""
    calls = [
        ("exec_command", {"cmd": "cd /testbed && python -m pytest tests/x.py"}, 4000),
        ("exec_command", {"cmd": "sed -n 1,80p a.py"}, 3000),
        ("code_show", {"name": "f"}, 1000),
        ("exec_command", {"cmd": "ls"}, 1000),
    ]
    lines = []
    for number, (name, arguments, size) in enumerate(calls):
        call = {"type": "function_call", "name": name, "call_id": f"call_{number}", "arguments": json.dumps(arguments)}
        output = {"type": "function_call_output", "call_id": f"call_{number}", "output": "x" * size}
        usage = {"last_token_usage": {"input_tokens": 12000 + 3000 * number}}
        lines.append({"type": "response_item", "payload": call})
        lines.append({"type": "response_item", "payload": output})
        lines.append({"type": "event_msg", "payload": {"type": "token_count", "info": usage}})
    run = tmp_path / "run"
    rollout = run / "scratch" / "a__1" / "codex-home" / "sessions" / "2026" / "10" / "02" / "rollout-x.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return str(run)


def test_kinds_sums_tool_output_per_kind_of_call(tmp_path, capsys):
    module = load()
    module.kinds(a_session(tmp_path))
    out = capsys.readouterr().out
    assert "4 calls, 9000 chars, 0 compactions" in out
    assert "shell: tests" in out and "44.4%" in out
    assert "shell: file reads" in out and "33.3%" in out
    assert "index: code_show" in out and "shell: search and listing" in out


def test_mask_replays_the_default_policy_unless_one_is_given(tmp_path, capsys, monkeypatch):
    module = load()
    run = a_session(tmp_path)
    monkeypatch.setattr(module.sys, "argv", ["context_budget_replay.py", "mask", run])
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "masked 0.85/0.50/16K" in out and "masked 0.82/0.55/8K" in out
    assert all(f"limit {limit:6d}" in out for limit in module.LIMITS)
    argv = ["context_budget_replay.py", "mask", "--policy", "0.9,0.4,12000", run]
    monkeypatch.setattr(module.sys, "argv", argv)
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "masked 0.90/0.40/12K" in out and "+ old commands 0.90/0.40/12K" in out
    assert "masked 0.85" not in out


def test_the_replay_masks_by_the_same_rule_as_the_crate():
    """The replay's default policy and limits of §4.1 are the masking crate's constants."""
    module = load()
    source = (Path(ROOT) / "ling-rs" / "masking" / "src" / "lib.rs").read_text()
    constants = re.findall(r"pub const (\w+): \w+ = ([\d_]+);", source)
    number = {name: int(value.replace("_", "")) for name, value in constants}
    high, low, step = module.POLICIES[0]
    assert (number["HIGH_PERCENT"], number["LOW_PERCENT"], number["MIN_STEP_TOKENS"]) == (
        round(high * 100),
        round(low * 100),
        step,
    )
    assert number["KEEP_RECENT"] == module.KEEP_RECENT
    assert number["MIN_MASKABLE_CHARS"] == module.MIN_MASKABLE_CHARS
    assert number["MAX_PLACEHOLDER_CHARS"] == module.PLACEHOLDER_CHARS
