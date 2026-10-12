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


def test_the_apply_patch_arm_reaches_only_the_candidates_round(tmp_path):
    status, recorded, folder = night(tmp_path, "default apply-patch-function")
    assert status == 0 and (folder / "done").exists()
    record, candidate = runs(recorded)
    assert "--name n4-default " in record and "--apply-patch" not in record
    assert "--name n4-apply-patch-function " in candidate and candidate.count("--apply-patch function") == 1
    assert "--task-rules" not in candidate
    assert "swe-bench report n4-apply-patch-function --against n4-default" in recorded
    # It stacks with the other arms like any part.
    stacked = tmp_path / "stacked"
    stacked.mkdir()
    _, recorded, _ = night(stacked, "default restart-2+apply-patch-freeform")
    candidate = runs(recorded)[1]
    assert "--restart-after-compactions 2 --apply-patch freeform" in candidate


@pytest.mark.parametrize("arm", ["apply-patch", "apply-patch-func", "apply_patch-function"])
def test_a_misspelt_apply_patch_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default {arm}")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


def test_night_6s_record_arm_is_the_freeform_exec_tool_and_the_candidate_the_launchers_choice(tmp_path):
    status, recorded, folder = night(tmp_path, "exec-tool-freeform default")
    assert status == 0 and (folder / "done").exists()
    record, candidate = runs(recorded)
    assert "--name n4-exec-tool-freeform " in record and record.count("--exec-tool freeform") == 1
    assert "--name n4-default " in candidate and "--exec-tool" not in candidate
    assert "--task-rules" not in record and "--task-rules" not in candidate
    assert "swe-bench report n4-default --against n4-exec-tool-freeform" in recorded
    assert (folder / "default-against-exec-tool-freeform.txt").exists()
    # The forced form, and stacking with the other arms like any part.
    stacked = tmp_path / "stacked"
    stacked.mkdir()
    _, recorded, _ = night(stacked, "default restart-2+exec-tool-function")
    assert "--restart-after-compactions 2 --exec-tool function" in runs(recorded)[1]


@pytest.mark.parametrize("arm", ["exec-tool", "exec-tool-auto", "exec-tool-off", "exec_tool-function", "exec-tool-func"])
def test_a_misspelt_exec_tool_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"{arm} default")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


@pytest.mark.parametrize("reads_it", [False, True])
def test_start_refuses_an_installed_ling_that_does_not_read_the_exec_tool_switch(tmp_path, reads_it):
    # `start` with stand-ins: no unit is active (systemctl), git succeeds and changes nothing,
    # and the venv's Python names a fake `ling` and an install folder without the indexers.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ling = tmp_path / "ling"
    ling.write_bytes(b"\x7fELF ... DREAMFERENCE_MIGHTLING_EXEC_TOOL ..." if reads_it else b"\x7fELF ... 1.6.0 ...")
    for name, body in (("systemctl", "exit 1"), ("git", "exit 0")):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/bash\n{body}\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\ncase "$2" in *executable_path*) echo "{ling}";; *) echo "{tmp_path / "install"}";; esac\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WT": ROOT, "PY": str(fake),
           "D": str(tmp_path / "night6"), "NIGHT": "6", "UNIT": "ling-swe-night6-test", "LIVE_UNIT": "ling-swe-night5-test",
           "ARMS": "exec-tool-freeform default", "LIST": str(tmp_path / "list.txt")}
    result = subprocess.run(["bash", SCRIPT, "start"], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    refused = f"{ling} does not read DREAMFERENCE_MIGHTLING_EXEC_TOOL" in result.stdout
    assert refused is not reads_it
    if reads_it:
        # Past the check, `start` stops at the next one: the indexers missing from the fake install.
        assert "ling-code is missing" in result.stdout
    assert not (tmp_path / "night6").exists()
