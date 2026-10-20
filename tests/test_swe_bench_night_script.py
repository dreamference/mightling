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
    status, recorded, folder = night(tmp_path, "default apply-patch-off")
    assert status == 0 and (folder / "done").exists()
    record, candidate = runs(recorded)
    assert "--name n4-default " in record and "--apply-patch" not in record
    assert "--name n4-apply-patch-off " in candidate and candidate.count("--apply-patch off") == 1
    assert "--task-rules" not in candidate
    assert "swe-bench report n4-apply-patch-off --against n4-default" in recorded
    # It stacks with the other arms like any part.
    stacked = tmp_path / "stacked"
    stacked.mkdir()
    _, recorded, _ = night(stacked, "default restart-2+apply-patch-freeform")
    candidate = runs(recorded)[1]
    assert "--restart-after-compactions 2 --apply-patch freeform" in candidate


# `apply-patch-function` too: the pinned Codex has no function form, and ling refuses to start with one.
@pytest.mark.parametrize("arm", ["apply-patch", "apply-patch-func", "apply_patch-function", "apply-patch-function"])
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


def test_night_10s_arms_pin_codexs_turn_end_key_off_and_on(tmp_path):
    status, recorded, folder = night(tmp_path, "turn-end-compaction-off turn-end-compaction-on")
    assert status == 0 and (folder / "done").exists()
    record, candidate = runs(recorded)
    assert "--name n4-turn-end-compaction-off " in record and record.count("--turn-end-compaction off") == 1
    assert "--name n4-turn-end-compaction-on " in candidate and candidate.count("--turn-end-compaction on") == 1
    assert "--task-rules" not in record and "--task-rules" not in candidate
    assert "swe-bench report n4-turn-end-compaction-on --against n4-turn-end-compaction-off" in recorded
    assert (folder / "turn-end-compaction-on-against-turn-end-compaction-off.txt").exists()
    # A percentage of the usable window, and stacking with the other arms like any part.
    stacked = tmp_path / "stacked"
    stacked.mkdir()
    _, recorded, _ = night(stacked, "turn-end-compaction-off turn-end-compaction-20+exec-tool-function")
    assert "--turn-end-compaction 20 --exec-tool function" in runs(recorded)[1]


@pytest.mark.parametrize("arm", ["turn-end-compaction", "turn-end-compaction-auto", "turn_end_compaction-on",
                                 "turn-end-compaction-0", "turn-end-compaction-100", "turn-end-on"])
def test_a_misspelt_turn_end_compaction_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"{arm} turn-end-compaction-on")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


def test_start_refuses_a_turn_end_night_where_only_one_arm_pins_the_key(tmp_path):
    # In `exec` the key is off unless an arm pins it: `default` beside `turn-end-compaction-off`
    # would measure off twice. Nothing is smoked and nothing is started.
    result, recorded, verdicts, units, folder = start(tmp_path, "turn-end-compaction-off default")
    assert result.returncode == 1 and "Arm default pins no turn-end compaction" in result.stdout
    assert runs(recorded) == [] and verdicts == [] and units == [] and not folder.exists()
    both = tmp_path / "both"
    both.mkdir()
    result, recorded, verdicts, units, folder = start(both, "turn-end-compaction-off turn-end-compaction-on")
    assert result.returncode == 0, result.stdout + result.stderr
    record, candidate = runs(recorded)
    assert "--name n7-smoke-turn-end-compaction-off-" in record and record.count("--turn-end-compaction off") == 1
    assert "--name n7-smoke-turn-end-compaction-on-" in candidate and candidate.count("--turn-end-compaction on") == 1
    assert len(units) == 1


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


def start(tmp_path, arms, smoke_fails=None, task_timeout=""):
    """
    Runs `start` with stand-ins for everything it reaches: no unit is active (systemctl), git
    succeeds and changes nothing, systemd-run records its arguments, and the venv's Python names a
    fake `ling` and a fake install folder, records every `ling-admin` call and every smoke verdict,
    and fails the verdict of the arm named in `smoke_fails`. Returns (result, admin calls, verdicts,
    systemd-run calls, D).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ling = tmp_path / "ling"
    ling.write_bytes(b"\x7fELF ... DREAMFERENCE_MIGHTLING_EXEC_TOOL DREAMFERENCE_MIGHTLING_APPLY_PATCH DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS ...")
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    for tool in ("ling-code", "codebase-memory-mcp"):
        (install / "bin" / tool).write_text("#!/bin/bash\n")
        (install / "bin" / tool).chmod(0o755)
    units = tmp_path / "systemd-run.txt"
    for name, body in (("systemctl", "exit 1"), ("git", "exit 0"), ("systemd-run", f'echo "$*" >> "{units}"')):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/bash\n{body}\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    calls, verdicts = tmp_path / "calls.txt", tmp_path / "verdicts.txt"
    fake = tmp_path / "python"
    fake.write_text(f"""#!/bin/bash
case "$2" in
    *executable_path*) echo "{ling}";;
    *INSTALL_DIR*) echo "{install}";;
    *SweBenchArmSmoke*) shift 2; echo "$* $PYTHONPATH" >> "{verdicts}"
        [ "$4" = "{smoke_fails}" ] && {{ echo "❌ Smoke check of arm $4: no answer from the model."; exit 1; }}
        echo "✅ Smoke check of arm $4"; exit 0;;
    *) shift 2; echo "$*" >> "{calls}";;
esac
""")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("acme__widget-1\nacme__widget-2\n")
    folder = tmp_path / "night7"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WT": ROOT, "PY": str(fake),
           "D": str(folder), "NIGHT": "7", "PREFIX": "n7", "UNIT": "ling-swe-night7-test",
           "LIVE_UNIT": "ling-swe-night6-test", "ARMS": arms, "LIST": str(task_list), "RESERVE_GB": "0",
           "TASK_TIMEOUT": task_timeout}
    result = subprocess.run(["bash", SCRIPT, "start"], env=env, capture_output=True, text=True, timeout=60)
    read = lambda path: path.read_text().splitlines() if path.exists() else []  # noqa: E731
    return result, read(calls), read(verdicts), read(units), folder


def test_start_smokes_every_arm_with_its_own_options_before_the_unit(tmp_path):
    result, recorded, verdicts, units, folder = start(tmp_path, "default exec-tool-function", task_timeout="60m")
    assert result.returncode == 0, result.stdout + result.stderr
    record, candidate = runs(recorded)
    # One real task per arm, a run of its own each, the arm's options and nothing else.
    assert "--instances sympy__sympy-13480 --name n7-smoke-default-" in record and "--exec-tool" not in record
    assert "--name n7-smoke-exec-tool-function-" in candidate and candidate.count("--exec-tool function") == 1
    assert "--code-index universal --mask off --prompt default" in candidate
    assert [line.split()[3] for line in verdicts] == ["default", "exec-tool-function"]
    # From the worktree, with the smoke's own config: a short limit and no nudges.
    run_name, _, _, _, pythonpath = verdicts[0].split()
    assert run_name in record and pythonpath == ROOT
    smoke_config = next((tmp_path / "smoke").glob("n7-*/dreamference.toml")).read_text()
    assert 'task_timeout = "6m"' in smoke_config and "nudges = 0" in smoke_config and "max_parallel = 1" in smoke_config
    # Only then the unit, with the night's own limit in its config.
    assert len(units) == 1 and "--unit=ling-swe-night7-test" in units[0] and "TASK_TIMEOUT=60m" in units[0]
    assert 'task_timeout = "60m"' in (folder / "dreamference.toml").read_text()
    assert "✅ Started ling-swe-night7-test" in result.stdout


def test_start_refuses_when_an_arm_fails_its_smoke_and_touches_nothing_else(tmp_path):
    result, recorded, verdicts, units, folder = start(tmp_path, "default drop-outputs+exec-tool-function",
                                                      smoke_fails="drop-outputs+exec-tool-function")
    assert result.returncode == 1
    assert "❌ Night 7 not started: arm drop-outputs+exec-tool-function failed its smoke check" in result.stdout
    assert len(runs(recorded)) == 2 and len(verdicts) == 2
    assert units == [] and not folder.exists()


def test_start_stops_at_the_first_arm_that_fails_its_smoke(tmp_path):
    result, recorded, verdicts, units, folder = start(tmp_path, "default tests-v2", smoke_fails="default")
    assert result.returncode == 1 and len(runs(recorded)) == 1 and len(verdicts) == 1
    assert units == [] and not folder.exists()
    # Without TASK_TIMEOUT a night keeps the runner's default limit.
    (tmp_path / "again").mkdir()
    result, _, _, _, folder = start(tmp_path / "again", "default tests-v2")
    assert result.returncode == 0 and "task_timeout" not in (folder / "dreamference.toml").read_text()
def test_the_drop_outputs_arm_reaches_only_its_round_and_stacks(tmp_path):
    status, recorded, folder = night(tmp_path, "default drop-outputs")
    assert status == 0 and (folder / "done").exists()
    record, candidate = runs(recorded)
    assert "--name n4-default " in record and "--drop-old-outputs" not in record
    assert "--name n4-drop-outputs " in candidate and candidate.count("--drop-old-outputs") == 1
    assert "--task-rules" not in record and "--task-rules" not in candidate
    assert "swe-bench report n4-drop-outputs --against n4-default" in recorded
    assert (folder / "drop-outputs-against-default.txt").exists()
    stacked = tmp_path / "stacked"
    stacked.mkdir()
    _, recorded, _ = night(stacked, "default drop-outputs+exec-tool-function")
    assert "--drop-old-outputs --exec-tool function" in runs(recorded)[1]


@pytest.mark.parametrize("arm", ["drop-output", "drop_outputs", "drop-outputs-on", "drop-old-outputs"])
def test_a_misspelt_drop_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default {arm}")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


@pytest.mark.parametrize("reads_it", [False, True])
def test_start_refuses_an_installed_ling_that_does_not_read_the_drop_switch(tmp_path, reads_it):
    # As for the Code Mode arm: stand-ins for systemctl, git and the venv's Python.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ling = tmp_path / "ling"
    ling.write_bytes(b"\x7fELF ... DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS ..." if reads_it else b"\x7fELF ... 1.6.0 ...")
    for name, body in (("systemctl", "exit 1"), ("git", "exit 0")):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/bash\n{body}\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\ncase "$2" in *executable_path*) echo "{ling}";; *) echo "{tmp_path / "install"}";; esac\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WT": ROOT, "PY": str(fake),
           "D": str(tmp_path / "night7"), "NIGHT": "7", "UNIT": "ling-swe-night7-test", "LIVE_UNIT": "ling-swe-night6-test",
           "ARMS": "default drop-outputs", "LIST": str(tmp_path / "list.txt")}
    result = subprocess.run(["bash", SCRIPT, "start"], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    refused = f"{ling} does not read DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS" in result.stdout
    assert refused is not reads_it
    if reads_it:
        assert "ling-code is missing" in result.stdout
    assert not (tmp_path / "night7").exists()


def best_of_night(tmp_path, arms="default", best_of="3"):
    """The night's `run` step with BEST_OF set; returns (exit status, recorded calls, D)."""
    calls = tmp_path / "calls.txt"
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\nshift 2\necho "$*" >> "{calls}"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("# two tasks\nacme__widget-1\nacme__widget-2\n")
    folder = tmp_path / "night11"
    folder.mkdir()
    env = {**os.environ, "WT": ROOT, "PY": str(fake), "LIST": str(task_list), "D": str(folder),
           "NIGHT": "11", "PREFIX": "n11", "ARMS": arms, "REFINE": "0", "BEST_OF": best_of,
           "CHOOSER_SERVER": "http://localhost:8000", "CHOOSER_SEED": "20261013"}
    result = subprocess.run(["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60)
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result.returncode, recorded, folder


def test_best_of_three_runs_the_arm_three_times_then_chooser_v1_and_the_report(tmp_path):
    status, recorded, folder = best_of_night(tmp_path)
    assert status == 0 and (folder / "done").exists() and not (folder / "failed").exists()
    rounds = runs(recorded)
    assert [r.split("--name ")[1].split()[0] for r in rounds] == ["n11-default-1", "n11-default-2", "n11-default-3"]
    assert all("--eval --remove-images" in r and "--task-rules" not in r for r in rounds)
    assert 'attempt 2 of 3' in rounds[1]
    # No drop-test-hunks regrade: the chooser reads the plain gradings.
    assert not any("--drop-test-hunks" in line for line in recorded)
    assert "swe-bench report n11-default-3 --against n11-default-1" in recorded
    # The chooser comes after every round, over the three, with v1's measured seed.
    chooser = next(i for i, line in enumerate(recorded) if "--runs n11-default-1,n11-default-2,n11-default-3 --seed" in line)
    assert chooser > max(recorded.index(r) for r in rounds)
    assert recorded[chooser].startswith("http://localhost:8000 --runs ") and "--seed 20261013" in recorded[chooser]
    assert f"--out {folder}/chooser-v1" in recorded[chooser]
    report = next(line for line in recorded if f"--chooser {folder}/chooser-v1/summary.json" in line)
    assert report.startswith("n11-default-1,n11-default-2,n11-default-3 ")


def test_best_of_takes_exactly_one_arm(tmp_path):
    status, recorded, folder = best_of_night(tmp_path, arms="default tests-v2")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


def test_without_best_of_the_night_is_the_two_arm_night(tmp_path):
    status, recorded, _ = best_of_night(tmp_path, arms="default apply-patch-off", best_of="1")
    assert status == 0
    assert [r.split("--name ")[1].split()[0] for r in runs(recorded)] == ["n11-default", "n11-apply-patch-off"]
    assert not any("--seed 20261013" in line for line in recorded)
