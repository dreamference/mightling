"""`scripts/swe_bench_night1.sh run`: how ARMS becomes each round's `swe-bench run` options.

The night's `run` step is driven with a stand-in for the venv's Python (`PY`) that records the
`ling-admin` arguments it is given and exits 0, so nothing is started: no agent, no container,
no model server. `start` (systemd-run, the worktree checkout) is never run here.
"""

import os
import stat
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "swe_bench_night1.sh")


def night(tmp_path, arms):
    """Runs the night's `run` step with `arms`; returns (exit status, recorded admin calls, D)."""
    calls = tmp_path / "calls.txt"
    fake = tmp_path / "python"
    # The script calls `"$PY" -c <code> <ling-admin arguments>`: drop the two, record the rest.
    fake.write_text(f'#!/bin/bash\nshift 2\necho "$*" >> "{calls}"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("# two tasks\nacme__widget-1\nacme__widget-2\n")
    folder = tmp_path / "night4"
    folder.mkdir()
    env = {**os.environ, "WT": ROOT, "PY": str(fake), "LIST": str(task_list), "D": str(folder),
           "NIGHT": "4", "PREFIX": "n4", "ARMS": arms, "REFINE": "0"}
    result = subprocess.run(["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60)
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result.returncode, recorded, folder


def runs(recorded):
    return [line for line in recorded if line.startswith("swe-bench run ")]


def test_the_restart_arm_reaches_only_the_candidates_round(tmp_path):
    status, recorded, folder = night(tmp_path, "default restart-2")
    assert status == 0 and (folder / "done").exists() and not (folder / "failed").exists()
    record, candidate = runs(recorded)
    assert "--name n4-default " in record
    assert "--restart-after-compactions" not in record and "--task-rules" not in record
    assert "--name n4-restart-2 " in candidate and candidate.count("--restart-after-compactions 2") == 1
    assert "--task-rules" not in candidate
    assert "swe-bench eval n4-default --drop-test-hunks --remove-images" in recorded
    assert "swe-bench eval n4-restart-2 --drop-test-hunks --remove-images" in recorded
    assert "swe-bench report n4-restart-2 --against n4-default" in recorded
    assert (folder / "restart-2-against-default.txt").exists()


def test_task_rules_and_the_restart_arm_stack_with_a_plus(tmp_path):
    status, recorded, _ = night(tmp_path, "tests-v2,issue-v1 tests-v2,issue-v1+restart-2")
    assert status == 0
    record, candidate = runs(recorded)
    assert "--name n4-tests-v2-issue-v1 " in record
    assert "--task-rules tests-v2,issue-v1" in record and "--restart-after-compactions" not in record
    assert "--name n4-tests-v2-issue-v1-restart-2 " in candidate
    assert "--task-rules tests-v2,issue-v1 --restart-after-compactions 2" in candidate


@pytest.mark.parametrize("arm", ["restart2", "restart-0", "restart-x"])
def test_a_misspelt_restart_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default {arm}")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()
