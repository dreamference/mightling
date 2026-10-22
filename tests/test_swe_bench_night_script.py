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


def test_the_engine_ab_serves_each_arm_on_the_remote_machine_and_restores_production(tmp_path):
    # A stand-in `ssh` on PATH records what the remote helper was asked and answers as it does; the stand-in
    # Python records each ling-admin call with the model server the round was pointed at.
    calls, remote = tmp_path / "calls.txt", tmp_path / "remote.txt"
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\nshift 2\necho "${{DREAMFERENCE_VLLM_HOST:-local}} $*" >> "{calls}"\n')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(f'#!/bin/bash\ncat > /dev/null\nargs="$*"\necho "$args" >> "{remote}"\n'
                   'case "$args" in *"serve sglang"*) echo "ready sglang" ;; *"serve engine"*) echo "ready ling-engine" ;;'
                   ' *restore*) echo restored ;; esac\n')
    for path in (fake, ssh):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("acme__widget-1\n")
    folder = tmp_path / "night12"
    folder.mkdir()
    arms = "exec-tool-function+apply-patch-off+on-sglang exec-tool-function+apply-patch-off+on-engine"
    env = {**os.environ, "WT": ROOT, "PY": str(fake), "LIST": str(task_list), "D": str(folder), "NIGHT": "12",
           "PREFIX": "n12", "ARMS": arms, "REFINE": "0", "PATH": f"{bindir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text().splitlines()
    rounds = [line for line in recorded if " swe-bench run " in line]
    assert rounds[0].startswith("http://node-b.local:30000 swe-bench run ")
    assert "--name n12-exec-tool-function-apply-patch-off-on-sglang " in rounds[0]
    assert rounds[1].startswith("http://node-b.local:8300 swe-bench run ")
    assert all("--exec-tool function" in r and "--apply-patch off" in r and "on-" not in r.split("--name")[0] for r in rounds)
    asked = remote.read_text().splitlines()
    # One restore once both rounds have run: the EXIT trap's is cleared then.
    assert [line.split("serve ")[-1] if "serve" in line else "restore" for line in asked] == ["sglang", "engine", "restore"]
    assert all(line.startswith("-o BatchMode=yes node-b bash -s -- ") for line in asked)
    # The regrades need no model server and run after production is back.
    assert all(line.startswith("local ") for line in recorded if " swe-bench eval " in line)


@pytest.mark.parametrize("arm", ["on-vllm", "default+on-"])
def test_a_misspelt_model_server_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default {arm}")
    assert status == 2 and not runs(recorded) and (folder / "failed").exists()


HELPER = os.path.join(ROOT, "scripts", "engine_ab_remote.sh")


@pytest.fixture
def remote(tmp_path):
    """node-b in miniature for scripts/engine_ab_remote.sh: stand-in `docker` (m0-prod8000 running),
    `curl` (every port answers) and `ss` (every port free) on PATH, a drafter and target snapshot of its own, and
    the GPU lock under the test's folder. Returns (run, docker calls, lock path, AB_DIR); any lease is killed after."""
    bindir, state, ab_dir = tmp_path / "bin", tmp_path / "state", tmp_path / "ab-n12"
    for folder in (bindir, state, ab_dir):
        folder.mkdir()
    (state / "m0").touch()
    calls = tmp_path / "docker.txt"
    stubs = {
        "docker": f'#!/bin/bash\necho "$*" >> "{calls}"\ncase "$1" in\n'
                  f'  ps) case "$*" in *m0-prod8000*) [ -e "{state}/m0" ] && echo c0ffee ;; esac ;;\n'
                  f'  stop) rm -f "{state}/m0" ;;\n  start) touch "{state}/m0" ;;\n  inspect) echo true ;;\nesac\n',
        "curl": "#!/bin/bash\necho '{\"data\": [{\"id\": \"m\", \"owned_by\": \"sglang\"}]}'\n",
        "ss": "#!/bin/bash\nexit 0\n",
    }
    for name, body in stubs.items():
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    (ab_dir / "serve.py").write_text("print('docker run ...')\n")
    draft, model = tmp_path / "drafter", tmp_path / "snapshot"
    draft.mkdir()
    model.mkdir()
    (draft / "model.safetensors").write_bytes(b"drafter")
    digest = subprocess.run(["sha256sum", str(draft / "model.safetensors")], capture_output=True, text=True).stdout.split()[0]
    lock = tmp_path / ".ling-gpu.lock"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path), "AB_DIR": str(ab_dir),
           "MODEL_DIR": str(model), "DRAFT": str(draft), "DRAFT_SHA256": digest, "GPU_LOCK": str(lock),
           "GPU_LOCK_WAIT": "3", "SETTLE_S": "0"}

    def run(*args, **extra):
        with open(HELPER) as script:
            return subprocess.run(["bash", "-s", "--", *args], stdin=script, env={**env, **extra},
                                  capture_output=True, text=True, timeout=60)

    yield run, calls, lock, ab_dir
    for pid_file in tmp_path.glob("ab-*/gpu-lease.pid"):
        subprocess.run(["kill", pid_file.read_text().strip()], capture_output=True)


def lock_free(lock):
    return subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0


def wait_until(check, seconds=10.0):
    import time
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.2)
    return check()


def test_the_night_holds_the_engines_gpu_lock_from_its_first_serve_until_production_is_back(remote):
    run, calls, lock, ab_dir = remote
    first = run("serve", "sglang")
    assert first.returncode == 0 and "ready sglang" in first.stdout, first.stderr
    # The lock outlives the ssh session that took it: day-time engine work waits for the whole arm.
    assert not lock_free(lock)
    assert "stop m0-prod8000" in calls.read_text()
    # The next round's serve reuses the night's lease instead of waiting on itself (GPU_LOCK_WAIT is 3 s).
    second = run("serve", "sglang")
    assert second.returncode == 0 and "already the night's" in second.stderr, second.stderr
    assert not lock_free(lock)
    restored = run("restore")
    assert restored.returncode == 0 and "restored" in restored.stdout
    # Production is started first, then the lock is let go.
    assert wait_until(lambda: lock_free(lock))
    assert "start m0-prod8000" in calls.read_text()
    assert not (ab_dir / "gpu-lease.pid").exists()


def test_serve_waits_for_engine_work_holding_the_gpu_lock_and_touches_nothing_without_it(remote):
    import fcntl
    run, calls, lock, ab_dir = remote
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = run("serve", "sglang")
    assert result.returncode == 1 and "was not free within 3 s" in result.stderr
    assert "m0-prod8000" not in (calls.read_text() if calls.exists() else "").replace("ps -q -f name=^m0-prod8000$", "")
    assert not (ab_dir / "gpu-lease.pid").exists()


def test_a_lease_no_restore_ends_lets_the_gpu_lock_go_by_itself(remote):
    run, calls, lock, ab_dir = remote
    assert run("serve", "sglang", HOLD_MAX="1").returncode == 0
    assert wait_until(lambda: lock_free(lock), seconds=15)


def test_the_night_folder_reaches_the_helper_and_the_unit(tmp_path):
    calls, remote = tmp_path / "calls.txt", tmp_path / "remote.txt"
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\nshift 2\necho "$*" >> "{calls}"\n')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(f'#!/bin/bash\ncat > /dev/null\necho "$*" >> "{remote}"\n'
                   'case "$*" in *"serve sglang"*) echo "ready sglang" ;; *"serve engine"*) echo "ready ling-engine" ;; *) echo restored ;; esac\n')
    for path in (fake, ssh):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("acme__widget-1\n")
    folder = tmp_path / "night15"
    folder.mkdir()
    env = {**os.environ, "WT": ROOT, "PY": str(fake), "LIST": str(task_list), "D": str(folder), "NIGHT": "15",
           "PREFIX": "n15", "ARMS": "default+on-sglang default+on-engine", "REFINE": "0",
           "REMOTE_AB_DIR": "/home/user/ab-n15", "PATH": f"{bindir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    asked = remote.read_text().splitlines()
    assert asked and all(line.startswith("-o BatchMode=yes node-b AB_DIR=/home/user/ab-n15 bash -s -- ") for line in asked)
    # The unit gets the remote's names with the rest of the night's settings.
    with open(SCRIPT) as script:
        body = script.read()
    assert '--setenv=REMOTE_AB_DIR="$REMOTE_AB_DIR"' in body and '--setenv=REMOTE="$REMOTE"' in body


def test_restore_keep_stops_the_arms_and_keeps_the_gpu_lock_for_the_night(remote):
    run, calls, lock, ab_dir = remote
    assert run("serve", "sglang").returncode == 0
    kept = run("restore", "keep")
    assert kept.returncode == 0 and "kept" in kept.stdout, kept.stderr
    assert not lock_free(lock)
    assert "start m0-prod8000" not in calls.read_text() and "rm -f ab-n12-sglang" in calls.read_text()
    assert run("serve", "engine", GPU_LOCK_WAIT="1").stderr.count("already the night's") == 1
    assert run("restore").returncode == 0
    assert wait_until(lambda: lock_free(lock))


def test_restore_keep_with_no_lease_is_a_plain_restore(remote):
    run, calls, lock, ab_dir = remote
    restored = run("restore", "keep")
    assert restored.returncode == 0 and "restored" in restored.stdout
    assert wait_until(lambda: "start m0-prod8000" in calls.read_text())


def test_the_next_night_adopts_the_lease_so_the_gpu_lock_is_never_let_go(remote, tmp_path):
    run, calls, lock, earlier = remote
    later = tmp_path / "ab-n15"
    later.mkdir()
    (later / "serve.py").write_text("print('docker run ...')\n")
    assert run("serve", "sglang").returncode == 0
    lease = (earlier / "gpu-lease.pid").read_text().strip()
    adopted = run("adopt", str(earlier), AB_DIR=str(later))
    assert adopted.returncode == 0 and f"adopted {lease}" in adopted.stdout, adopted.stderr
    assert (later / "gpu-lease.pid").read_text().strip() == lease
    decoy = (earlier / "gpu-lease.pid").read_text().strip()
    assert decoy != lease
    # The earlier night's restore ends its decoy and brings production back; the lock stays taken.
    assert run("restore").returncode == 0
    assert wait_until(lambda: "start m0-prod8000" in calls.read_text() and not (earlier / "gpu-lease.pid").exists())
    assert subprocess.run(["kill", "-0", decoy], capture_output=True).returncode != 0
    assert not lock_free(lock)
    # The later night serves under the adopted lease, with its own SGLang container's name, then lets the lock go.
    served = run("serve", "sglang", AB_DIR=str(later), GPU_LOCK_WAIT="1")
    assert served.returncode == 0 and "already the night's" in served.stderr, served.stderr
    assert "rm -f ab-n15-sglang" in calls.read_text()
    assert run("restore", AB_DIR=str(later)).returncode == 0
    assert wait_until(lambda: lock_free(lock))


def test_adopt_refuses_without_a_live_lease_to_take(remote, tmp_path):
    run, calls, lock, earlier = remote
    later = tmp_path / "ab-n15"
    later.mkdir()
    refused = run("adopt", str(earlier), AB_DIR=str(later))
    assert refused.returncode == 1 and "no live lease" in refused.stderr
    assert not (later / "gpu-lease.pid").exists()


def disk_night(tmp_path, free_after_stop, **extra):
    """A night whose first round stops short once ("3 not run yet") with `free_after_stop` GB left on a stand-in df,
    which reads 500 GB once the next poll comes. Returns (result, recorded admin calls, D)."""
    calls, state = tmp_path / "calls.txt", tmp_path / "state"
    state.mkdir()
    (state / "free").write_text(f"{free_after_stop}\n")
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\nshift 2\necho "$*" >> "{calls}"\n'
                    f'case "$*" in "swe-bench report n4-default") '
                    f'[ -e "{state}/resumed" ] || {{ [ -e "{state}/stopped" ] && echo "INCOMPLETE          3 not run yet"; }} ;;\n'
                    f'  "swe-bench run "*n4-default\\ *) [ -e "{state}/stopped" ] && touch "{state}/resumed"; touch "{state}/stopped" ;; esac\n')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "df").write_text(f'#!/bin/bash\necho Avail\necho "$(cat "{state}/free")G"\n')
    (bindir / "sleep").write_text(f'#!/bin/bash\necho 500 > "{state}/free"\n')
    for path in (fake, bindir / "df", bindir / "sleep"):
        path.chmod(0o755)
    task_list = tmp_path / "list.txt"
    task_list.write_text("acme__widget-1\n")
    folder = tmp_path / "night4"
    folder.mkdir()
    env = {**os.environ, "WT": ROOT, "PY": str(fake), "LIST": str(task_list), "D": str(folder), "NIGHT": "4",
           "PREFIX": "n4", "ARMS": "default tests-v2", "REFINE": "0", "PATH": f"{bindir}:{os.environ['PATH']}",
           "DISK_POLL_S": "1", **extra}
    result = subprocess.run(["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60)
    return result, calls.read_text().splitlines(), folder


def test_a_round_stopped_for_disk_pauses_the_night_and_resumes_the_same_round(tmp_path):
    result, recorded, folder = disk_night(tmp_path, 90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [r.split("--name ")[1].split()[0] for r in runs(recorded)] == ["n4-default", "n4-default", "n4-tests-v2"]
    assert "paused: 90G free, the reserve is 100G" in result.stdout and "n4-default resumed: 500G free" in result.stdout
    assert not (folder / "failed").exists()


def test_a_round_that_stops_short_with_the_disk_fine_still_fails_the_night(tmp_path):
    result, recorded, folder = disk_night(tmp_path, 300)
    assert result.returncode == 1 and (folder / "failed").exists()
    assert "instances not run" in (folder / "failed").read_text() and len(runs(recorded)) == 1


def test_a_disk_that_stays_short_fails_the_night_after_the_pause_bound(tmp_path):
    result, recorded, folder = disk_night(tmp_path, 90, DISK_PAUSE_MAX_S="0")
    assert result.returncode == 1 and (folder / "failed").exists()
    assert "the disk stayed short" in result.stdout and len(runs(recorded)) == 1
