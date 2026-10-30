"""`scripts/swe_bench_night1.sh run`: how ARMS becomes each round's `swe-bench run` options.

The night's `run` step is driven with a stand-in for the venv's Python (`PY`) that records the
`ling-admin` arguments it is given and exits 0, so nothing is started: no agent, no container,
no model server. `start` (systemd-run, the worktree checkout) is never run here.
"""

import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "swe_bench_night1.sh")


def night(tmp_path, arms, ling=None, **extra):
    """Runs the night's `run` step with `arms`; returns (exit status, recorded admin calls, D)."""
    calls = tmp_path / "calls.txt"
    fake = tmp_path / "python"
    # The script calls `"$PY" -c <code> <ling-admin arguments>`: drop the two, record the rest; asked
    # for the installed ling, it names `ling`.
    fake.write_text(
        f'#!/bin/bash\ncase "$2" in *executable_path*) echo "{ling}"; exit 0;; esac\n'
        f'shift 2\necho "$*" >> "{calls}"\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("# two tasks\nacme__widget-1\nacme__widget-2\n")
    folder = tmp_path / "night4"
    folder.mkdir()
    # systemctl never reaches this machine's units: it lists INDEX_SCOPES as active index runs and records stops.
    stand_ins = tmp_path / "stand-ins"
    stand_ins.mkdir(exist_ok=True)
    (stand_ins / "systemctl").write_text(
        "#!/bin/bash\n"
        'case "$*" in *list-units*mightling-index*)\n'
        '    for u in $INDEX_SCOPES; do echo "$u loaded active running x"; done ;;\n'
        f'  *" stop "*) echo "$*" >> "{tmp_path / "systemctl.txt"}" ;; esac\nexit 0\n'
    )
    (stand_ins / "systemctl").chmod(0o755)
    env = {
        **os.environ,
        "WT": ROOT,
        "PY": str(fake),
        "LIST": str(task_list),
        "D": str(folder),
        "NIGHT": "4",
        "PREFIX": "n4",
        "ARMS": arms,
        "REFINE": "0",
        **extra,
    }
    env["PATH"] = f"{stand_ins}:{env.get('PATH', os.environ['PATH'])}"
    result = subprocess.run(
        ["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60
    )
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
    assert (
        "--name n4-restart-2 " in candidate
        and candidate.count("--restart-after-compactions 2") == 1
    )
    assert "--task-rules" not in candidate
    assert (
        "swe-bench eval n4-default --drop-test-hunks --remove-images --image-chunk 25" in recorded
    )
    assert (
        "swe-bench eval n4-restart-2 --drop-test-hunks --remove-images --image-chunk 25" in recorded
    )
    assert "swe-bench report n4-restart-2 --against n4-default" in recorded
    assert (folder / "restart-2-against-default.txt").exists()


def test_task_rules_and_the_restart_arm_stack_with_a_plus(tmp_path):
    status, recorded, _ = night(tmp_path, "tests-v2,issue-v1 tests-v2,issue-v1+restart-2")
    assert status == 0
    record, candidate = runs(recorded)
    assert "--name n4-tests-v2-issue-v1 " in record
    assert (
        "--task-rules tests-v2,issue-v1" in record and "--restart-after-compactions" not in record
    )
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
@pytest.mark.parametrize(
    "arm", ["apply-patch", "apply-patch-func", "apply_patch-function", "apply-patch-function"]
)
def test_a_misspelt_apply_patch_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default {arm}")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


def test_night_6s_record_arm_is_the_freeform_exec_tool_and_the_candidate_the_launchers_choice(
    tmp_path,
):
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


@pytest.mark.parametrize(
    "arm", ["exec-tool", "exec-tool-auto", "exec-tool-off", "exec_tool-function", "exec-tool-func"]
)
def test_a_misspelt_exec_tool_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"{arm} default")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


def test_night_10s_arms_pin_codexs_turn_end_key_off_and_on(tmp_path):
    status, recorded, folder = night(tmp_path, "turn-end-compaction-off turn-end-compaction-on")
    assert status == 0 and (folder / "done").exists()
    record, candidate = runs(recorded)
    assert (
        "--name n4-turn-end-compaction-off " in record
        and record.count("--turn-end-compaction off") == 1
    )
    assert (
        "--name n4-turn-end-compaction-on " in candidate
        and candidate.count("--turn-end-compaction on") == 1
    )
    assert "--task-rules" not in record and "--task-rules" not in candidate
    assert (
        "swe-bench report n4-turn-end-compaction-on --against n4-turn-end-compaction-off"
        in recorded
    )
    assert (folder / "turn-end-compaction-on-against-turn-end-compaction-off.txt").exists()
    # A percentage of the usable window, and stacking with the other arms like any part.
    stacked = tmp_path / "stacked"
    stacked.mkdir()
    _, recorded, _ = night(
        stacked, "turn-end-compaction-off turn-end-compaction-20+exec-tool-function"
    )
    assert "--turn-end-compaction 20 --exec-tool function" in runs(recorded)[1]


@pytest.mark.parametrize(
    "arm",
    [
        "turn-end-compaction",
        "turn-end-compaction-auto",
        "turn_end_compaction-on",
        "turn-end-compaction-0",
        "turn-end-compaction-100",
        "turn-end-on",
    ],
)
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
    result, recorded, verdicts, units, folder = start(
        both, "turn-end-compaction-off turn-end-compaction-on"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record, candidate = runs(recorded)
    assert (
        "--name n7-smoke-turn-end-compaction-off-" in record
        and record.count("--turn-end-compaction off") == 1
    )
    assert (
        "--name n7-smoke-turn-end-compaction-on-" in candidate
        and candidate.count("--turn-end-compaction on") == 1
    )
    assert len(units) == 1


@pytest.mark.parametrize("reads_it", [False, True])
def test_start_refuses_an_installed_ling_that_does_not_read_the_exec_tool_switch(
    tmp_path, reads_it
):
    # `start` with stand-ins: no unit is active (systemctl), git succeeds and changes nothing,
    # and the venv's Python names a fake `ling` and an install folder without the indexers.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ling = tmp_path / "ling"
    ling.write_bytes(
        b"\x7fELF ... DREAMFERENCE_MIGHTLING_EXEC_TOOL ..."
        if reads_it
        else b"\x7fELF ... 1.6.0 ..."
    )
    for name, body in (("systemctl", "exit 1"), ("git", "exit 0")):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/bash\n{body}\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    fake = tmp_path / "python"
    fake.write_text(
        f'#!/bin/bash\ncase "$2" in *executable_path*) echo "{ling}";; *) echo "{tmp_path / "install"}";; esac\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "WT": ROOT,
        "PY": str(fake),
        "D": str(tmp_path / "night6"),
        "NIGHT": "6",
        "UNIT": "ling-swe-night6-test",
        "LIVE_UNIT": "ling-swe-night5-test",
        "ARMS": "exec-tool-freeform default",
        "LIST": str(tmp_path / "list.txt"),
    }
    result = subprocess.run(
        ["bash", SCRIPT, "start"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 1
    refused = f"{ling} does not read DREAMFERENCE_MIGHTLING_EXEC_TOOL" in result.stdout
    assert refused is not reads_it
    if reads_it:
        # Past the check, `start` stops at the next one: the indexers missing from the fake install.
        assert "ling-code is missing" in result.stdout
    assert not (tmp_path / "night6").exists()


def start(
    tmp_path,
    arms,
    smoke_fails=None,
    task_timeout="",
    tasks=("acme__widget-1", "acme__widget-2"),
    free_gb=None,
    **extra,
):
    """Runs `start` with stand-ins for everything it reaches.

    No unit is active (systemctl), git succeeds and changes nothing, systemd-run records its
    arguments, and the venv's Python names a fake `ling` and a fake install folder, records every `ling-admin` call and every smoke verdict,
    and fails the verdict of the arm named in `smoke_fails`. With `free_gb`, `df` says that much is
    free. git answers `rev-parse HEAD` with REF and records its calls in git.txt. Returns (result, admin
    calls, verdicts, systemd-run calls, D). `ling_bytes` (in `extra`) is the fake `ling`'s content.
    """
    ling_bytes = extra.pop("ling_bytes", None)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ling = tmp_path / "ling"
    ling.write_bytes(
        ling_bytes
        or b"\x7fELF ... DREAMFERENCE_MIGHTLING_EXEC_TOOL DREAMFERENCE_MIGHTLING_APPLY_PATCH "
        b"DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS ..."
    )
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    for tool in ("ling-code", "codebase-memory-mcp"):
        (install / "bin" / tool).write_text("#!/bin/bash\n")
        (install / "bin" / tool).chmod(0o755)
    units = tmp_path / "systemd-run.txt"
    git_log = tmp_path / "git.txt"
    git_body = (
        f'echo "$*" >> "{git_log}"\ncase "$*" in *rev-parse*HEAD*) echo "$REF";; esac\nexit 0'
    )
    # systemctl: no unit is active, except those named in ACTIVE_UNITS, which `list-units` lists.
    systemctl_body = (
        'if [ "$2" = list-units ]; then\n'
        '  for u in $ACTIVE_UNITS; do echo "$u loaded active running x"; done; exit 0\n'
        "fi\n"
        "exit 1"
    )
    tools = [
        ("systemctl", systemctl_body),
        ("git", git_body),
        ("systemd-run", f'echo "$*" >> "{units}"'),
    ]
    if free_gb is not None:
        tools.append(("df", f'echo "Avail"; echo " {free_gb}G"'))
    for name, body in tools:
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
    task_list.write_text("".join(f"{task}\n" for task in tasks))
    folder = tmp_path / "night7"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "WT": ROOT,
        "PY": str(fake),
        "D": str(folder),
        "NIGHT": "7",
        "PREFIX": "n7",
        "UNIT": "ling-swe-night7-test",
        "LIVE_UNIT": "ling-swe-night6-test",
        "ARMS": arms,
        "LIST": str(task_list),
        "RESERVE_GB": "0",
        "TASK_TIMEOUT": task_timeout,
        **extra,
    }
    result = subprocess.run(
        ["bash", SCRIPT, "start"], env=env, capture_output=True, text=True, timeout=60
    )
    read = lambda path: path.read_text().splitlines() if path.exists() else []  # noqa: E731
    return result, read(calls), read(verdicts), read(units), folder


def test_start_smokes_every_arm_with_its_own_options_before_the_unit(tmp_path):
    result, recorded, verdicts, units, folder = start(
        tmp_path, "default exec-tool-function", task_timeout="60m"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record, candidate = runs(recorded)
    # One real task per arm, a run of its own each, the arm's options and nothing else.
    assert (
        "--instances sympy__sympy-13480 --name n7-smoke-default-" in record
        and "--exec-tool" not in record
    )
    assert (
        "--name n7-smoke-exec-tool-function-" in candidate
        and candidate.count("--exec-tool function") == 1
    )
    assert "--code-index universal --mask off --prompt default" in candidate
    assert [line.split()[3] for line in verdicts] == ["default", "exec-tool-function"]
    # From the worktree, with the smoke's own config: a short limit and no nudges.
    run_name, _, _, _, pythonpath = verdicts[0].split()
    assert run_name in record and pythonpath == ROOT
    smoke_config = next((tmp_path / "smoke").glob("n7-*/dreamference.toml")).read_text()
    assert (
        'task_timeout = "6m"' in smoke_config
        and "nudges = 0" in smoke_config
        and "max_parallel = 1" in smoke_config
    )
    # Only then the unit, with the night's own limit in its config.
    assert (
        len(units) == 1
        and "--unit=ling-swe-night7-test" in units[0]
        and "TASK_TIMEOUT=60m" in units[0]
    )
    assert 'task_timeout = "60m"' in (folder / "dreamference.toml").read_text()
    assert "✅ Started ling-swe-night7-test" in result.stdout


def test_start_refuses_when_an_arm_fails_its_smoke_and_touches_nothing_else(tmp_path):
    result, recorded, verdicts, units, folder = start(
        tmp_path,
        "default drop-outputs+exec-tool-function",
        smoke_fails="drop-outputs+exec-tool-function",
    )
    assert result.returncode == 1
    assert (
        "❌ Night 7 not started: arm drop-outputs+exec-tool-function failed its smoke check"
        in result.stdout
    )
    assert len(runs(recorded)) == 2 and len(verdicts) == 2
    assert units == [] and not folder.exists()


def test_start_stops_at_the_first_arm_that_fails_its_smoke(tmp_path):
    result, recorded, verdicts, units, folder = start(
        tmp_path, "default tests-v2", smoke_fails="default"
    )
    assert result.returncode == 1 and len(runs(recorded)) == 1 and len(verdicts) == 1
    assert units == [] and not folder.exists()
    # Without TASK_TIMEOUT a night keeps the runner's default limit.
    (tmp_path / "again").mkdir()
    result, _, _, _, folder = start(tmp_path / "again", "default tests-v2")
    assert (
        result.returncode == 0 and "task_timeout" not in (folder / "dreamference.toml").read_text()
    )


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


@pytest.mark.parametrize(
    "arm", ["drop-output", "drop_outputs", "drop-outputs-on", "drop-old-outputs"]
)
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
    ling.write_bytes(
        b"\x7fELF ... DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS ..."
        if reads_it
        else b"\x7fELF ... 1.6.0 ..."
    )
    for name, body in (("systemctl", "exit 1"), ("git", "exit 0")):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/bash\n{body}\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    fake = tmp_path / "python"
    fake.write_text(
        f'#!/bin/bash\ncase "$2" in *executable_path*) echo "{ling}";; *) echo "{tmp_path / "install"}";; esac\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "WT": ROOT,
        "PY": str(fake),
        "D": str(tmp_path / "night7"),
        "NIGHT": "7",
        "UNIT": "ling-swe-night7-test",
        "LIVE_UNIT": "ling-swe-night6-test",
        "ARMS": "default drop-outputs",
        "LIST": str(tmp_path / "list.txt"),
    }
    result = subprocess.run(
        ["bash", SCRIPT, "start"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 1
    refused = f"{ling} does not read DREAMFERENCE_MIGHTLING_DROP_OLD_OUTPUTS" in result.stdout
    assert refused is not reads_it
    if reads_it:
        assert "ling-code is missing" in result.stdout
    assert not (tmp_path / "night7").exists()


def test_the_guard_arms_reach_only_their_round_and_stack(tmp_path):
    status, recorded, folder = night(tmp_path, "default test-guard+finish-check")
    assert status == 0 and (folder / "done").exists()
    record, candidate = runs(recorded)
    assert "--test-guard" not in record and "--finish-check" not in record
    assert "--name n4-test-guard-finish-check " in candidate
    assert candidate.count("--test-guard") == 1 and candidate.count("--finish-check") == 1
    assert "--task-rules" not in candidate
    assert (folder / "test-guard-finish-check-against-default.txt").exists()


@pytest.mark.parametrize(
    "arm", ["test-guards", "test_guard", "finish", "finish-checks", "testguard"]
)
def test_a_misspelt_guard_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default {arm}")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


@pytest.mark.parametrize(
    ("arm", "variable"), [("test-guard", "TEST_GUARD"), ("finish-check", "FINISH_CHECK")]
)
@pytest.mark.parametrize("reads_it", [False, True])
def test_start_refuses_an_installed_ling_without_the_guards(tmp_path, arm, variable, reads_it):
    # As for the drop arm: stand-ins for systemctl, git and the venv's Python.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ling = tmp_path / "ling"
    marker = f"DREAMFERENCE_MIGHTLING_{variable}".encode()
    ling.write_bytes(b"\x7fELF ... " + marker + b" ..." if reads_it else b"\x7fELF ... 1.7.0 ...")
    for name, body in (("systemctl", "exit 1"), ("git", "exit 0")):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/bash\n{body}\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    fake = tmp_path / "python"
    install = tmp_path / "install"
    fake.write_text(
        f'#!/bin/bash\ncase "$2" in *executable_path*) echo "{ling}";; *) echo "{install}";; esac\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "WT": ROOT,
        "PY": str(fake),
        "D": str(tmp_path / "night7"),
        "NIGHT": "7",
        "UNIT": "ling-swe-night7-test",
        "LIVE_UNIT": "ling-swe-night6-test",
        "ARMS": f"default {arm}",
        "LIST": str(tmp_path / "list.txt"),
    }
    result = subprocess.run(
        ["bash", SCRIPT, "start"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 1
    refused = f"{ling} does not read DREAMFERENCE_MIGHTLING_{variable}" in result.stdout
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
    env = {
        **os.environ,
        "WT": ROOT,
        "PY": str(fake),
        "LIST": str(task_list),
        "D": str(folder),
        "NIGHT": "11",
        "PREFIX": "n11",
        "ARMS": arms,
        "REFINE": "0",
        "BEST_OF": best_of,
        "CHOOSER_SERVER": "http://localhost:8000",
        "CHOOSER_SEED": "20261013",
    }
    result = subprocess.run(
        ["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60
    )
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result.returncode, recorded, folder


def test_best_of_three_runs_the_arm_three_times_then_chooser_v1_and_the_report(tmp_path):
    status, recorded, folder = best_of_night(tmp_path)
    assert status == 0 and (folder / "done").exists() and not (folder / "failed").exists()
    rounds = runs(recorded)
    assert [r.split("--name ")[1].split()[0] for r in rounds] == [
        "n11-default-1",
        "n11-default-2",
        "n11-default-3",
    ]
    assert all("--eval --remove-images" in r and "--task-rules" not in r for r in rounds)
    assert "attempt 2 of 3" in rounds[1]
    # No drop-test-hunks regrade: the chooser reads the plain gradings.
    assert not any("--drop-test-hunks" in line for line in recorded)
    assert "swe-bench report n11-default-3 --against n11-default-1" in recorded
    # The chooser comes after every round, over the three, with v1's measured seed.
    chooser = next(
        i
        for i, line in enumerate(recorded)
        if "--runs n11-default-1,n11-default-2,n11-default-3 --seed" in line
    )
    assert chooser > max(recorded.index(r) for r in rounds)
    assert (
        recorded[chooser].startswith("http://localhost:8000 --runs ")
        and "--seed 20261013" in recorded[chooser]
    )
    assert f"--out {folder}/chooser-v1" in recorded[chooser]
    report = next(
        line for line in recorded if f"--chooser {folder}/chooser-v1/summary.json" in line
    )
    assert report.startswith("n11-default-1,n11-default-2,n11-default-3 ")


def test_best_of_takes_exactly_one_arm(tmp_path):
    status, recorded, folder = best_of_night(tmp_path, arms="default tests-v2")
    assert status == 2 and runs(recorded) == []
    assert (folder / "failed").exists() and not (folder / "done").exists()


def test_without_best_of_the_night_is_the_two_arm_night(tmp_path):
    status, recorded, _ = best_of_night(tmp_path, arms="default apply-patch-off", best_of="1")
    assert status == 0
    assert [r.split("--name ")[1].split()[0] for r in runs(recorded)] == [
        "n11-default",
        "n11-apply-patch-off",
    ]
    assert not any("--seed 20261013" in line for line in recorded)


@pytest.mark.parametrize("overlay", [False, True])
def test_the_engine_ab_serves_each_arm_on_the_remote_machine_and_restores_production(
    tmp_path, overlay
):
    # A stand-in `ssh` on PATH records what the remote helper was asked and answers as it does; the stand-in
    # Python records each ling-admin call with the model server the round was pointed at.
    calls, remote = tmp_path / "calls.txt", tmp_path / "remote.txt"
    fake = tmp_path / "python"
    fake.write_text(
        f'#!/bin/bash\nshift 2\necho "${{DREAMFERENCE_VLLM_HOST:-local}} $*" >> "{calls}"\n'
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(
        f'#!/bin/bash\ncat > /dev/null\nargs="$*"\necho "$args" >> "{remote}"\n'
        'case "$args" in *"serve sglang"*) echo "ready sglang" ;; *"serve engine"*) echo "ready ling-engine" ;;'
        " *restore*) echo restored ;; esac\n"
    )
    getent = bindir / "getent"
    # The overlay's DNS knows the machine (a machine at another site) or it does not (this LAN, mDNS).
    getent.write_text(f'#!/bin/bash\n{"exit 0" if overlay else "exit 2"}\n')
    for path in (fake, ssh, getent):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("acme__widget-1\n")
    folder = tmp_path / "night12"
    folder.mkdir()
    arms = (
        "exec-tool-function+apply-patch-off+on-sglang exec-tool-function+apply-patch-off+on-engine"
    )
    env = {
        **os.environ,
        "WT": ROOT,
        "PY": str(fake),
        "LIST": str(task_list),
        "D": str(folder),
        "NIGHT": "12",
        "PREFIX": "n12",
        "ARMS": arms,
        "REFINE": "0",
        "REMOTE": "node-b",
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text().splitlines()
    rounds = [line for line in recorded if " swe-bench run " in line]
    host = "node-b.netbird.selfhosted" if overlay else "node-b.local"
    assert rounds[0].startswith(f"http://{host}:30000 swe-bench run ")
    assert "--name n12-exec-tool-function-apply-patch-off-on-sglang " in rounds[0]
    assert rounds[1].startswith(f"http://{host}:8300 swe-bench run ")
    assert all(
        "--exec-tool function" in r
        and "--apply-patch off" in r
        and "on-" not in r.split("--name")[0]
        for r in rounds
    )
    asked = remote.read_text().splitlines()
    # One restore once both rounds have run: the EXIT trap's is cleared then.
    assert [line.split("serve ")[-1] if "serve" in line else "restore" for line in asked] == [
        "sglang",
        "engine",
        "restore",
    ]
    ssh_name = "node-b.netbird.selfhosted" if overlay else "node-b"
    assert all(line.startswith(f"-o BatchMode=yes {ssh_name} bash -s -- ") for line in asked)
    # The regrades need no model server and run after production is back.
    assert all(line.startswith("local ") for line in recorded if " swe-bench eval " in line)


@pytest.mark.parametrize("arm", ["on-vllm", "default+on-"])
def test_a_misspelt_model_server_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default {arm}")
    assert status == 2 and not runs(recorded) and (folder / "failed").exists()


HELPER = f"{ROOT}/scripts/engine_ab_remote.sh"


@pytest.fixture
def remote(tmp_path):
    """The engine's machine in miniature for scripts/engine_ab_remote.sh.

    Stand-in `docker` (m0-prod8000 running), `curl` (every port answers) and `ss` (every port free) on
    PATH, a drafter and target snapshot of its own, and the GPU lock under the test's folder. Returns
    (run, docker calls, lock path, AB_DIR); any lease is killed after.
    """
    bindir, state, ab_dir = tmp_path / "bin", tmp_path / "state", tmp_path / "ab-n12"
    for folder in (bindir, state, ab_dir):
        folder.mkdir()
    (state / "m0").touch()
    calls = tmp_path / "docker.txt"
    stubs = {
        "docker": f'#!/bin/bash\necho "$*" >> "{calls}"\ncase "$1" in\n'
        f'  ps) case "$*" in *m0-prod8000*) [ -e "{state}/m0" ] && echo c0ffee ;; esac ;;\n'
        f'  stop) rm -f "{state}/m0" ;;\n  start) touch "{state}/m0" ;;\n'
        f'  inspect) case "$*" in *Mounts*) cat "{state}/mounts" 2>/dev/null ;; *) echo true ;; esac ;;\nesac\n',
        "curl": '#!/bin/bash\necho \'{"data": [{"id": "m", "owned_by": "sglang"}]}\'\n',
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
    digest = subprocess.run(
        ["sha256sum", str(draft / "model.safetensors")], capture_output=True, text=True
    ).stdout.split()[0]
    lock = tmp_path / ".ling-gpu.lock"
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "AB_DIR": str(ab_dir),
        "MODEL_DIR": str(model),
        "DRAFT": str(draft),
        "DRAFT_SHA256": digest,
        "GPU_LOCK": str(lock),
        "GPU_LOCK_WAIT": "3",
        "SETTLE_S": "0",
    }

    def run(*args, **extra):
        with pathlib.Path(HELPER).open() as script:
            return subprocess.run(
                ["bash", "-s", "--", *args],
                stdin=script,
                env={**env, **extra},
                capture_output=True,
                text=True,
                timeout=60,
            )

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
    with pathlib.Path(lock).open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = run("serve", "sglang")
    assert result.returncode == 1 and "was not free within 3 s" in result.stderr
    assert "m0-prod8000" not in (calls.read_text() if calls.exists() else "").replace(
        "ps -q -f name=^m0-prod8000$", ""
    )
    assert not (ab_dir / "gpu-lease.pid").exists()


def test_a_lease_no_restore_ends_lets_the_gpu_lock_go_by_itself(remote):
    run, _calls, lock, _ab_dir = remote
    assert run("serve", "sglang", HOLD_MAX="1").returncode == 0
    assert wait_until(lambda: lock_free(lock), seconds=15)


def test_the_night_folder_reaches_the_helper_and_the_unit(tmp_path):
    calls, remote = tmp_path / "calls.txt", tmp_path / "remote.txt"
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\nshift 2\necho "$*" >> "{calls}"\n')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(
        f'#!/bin/bash\ncat > /dev/null\necho "$*" >> "{remote}"\n'
        'case "$*" in *"serve sglang"*) echo "ready sglang" ;; *"serve engine"*) echo "ready ling-engine" ;;'
        " *) echo restored ;; esac\n"
    )
    for path in (fake, ssh):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    task_list = tmp_path / "list.txt"
    task_list.write_text("acme__widget-1\n")
    folder = tmp_path / "night15"
    folder.mkdir()
    env = {
        **os.environ,
        "WT": ROOT,
        "PY": str(fake),
        "LIST": str(task_list),
        "D": str(folder),
        "NIGHT": "15",
        "PREFIX": "n15",
        "ARMS": "default+on-sglang default+on-engine",
        "REFINE": "0",
        "REMOTE": "node-b",
        "REMOTE_AB_DIR": "/home/user/ab-n15",
        "PATH": f"{bindir}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    asked = remote.read_text().splitlines()
    assert asked and all(
        line.startswith("-o BatchMode=yes node-b AB_DIR=/home/user/ab-n15 bash -s -- ")
        for line in asked
    )
    # The unit gets the remote's names with the rest of the night's settings.
    with pathlib.Path(SCRIPT).open() as script:
        body = script.read()
    assert '--setenv=REMOTE_AB_DIR="$REMOTE_AB_DIR"' in body and '--setenv=REMOTE="$REMOTE"' in body


def test_restore_keep_stops_the_arms_and_keeps_the_gpu_lock_for_the_night(remote):
    run, calls, lock, _ab_dir = remote
    assert run("serve", "sglang").returncode == 0
    kept = run("restore", "keep")
    assert kept.returncode == 0 and "kept" in kept.stdout, kept.stderr
    assert not lock_free(lock)
    assert (
        "start m0-prod8000" not in calls.read_text() and "rm -f ab-n12-sglang" in calls.read_text()
    )
    assert run("serve", "engine", GPU_LOCK_WAIT="1").stderr.count("already the night's") == 1
    assert run("restore").returncode == 0
    assert wait_until(lambda: lock_free(lock))


def test_restore_keep_with_no_lease_is_a_plain_restore(remote):
    run, calls, _lock, _ab_dir = remote
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
    assert wait_until(
        lambda: (
            "start m0-prod8000" in calls.read_text() and not (earlier / "gpu-lease.pid").exists()
        )
    )
    assert subprocess.run(["kill", "-0", decoy], capture_output=True).returncode != 0
    assert not lock_free(lock)
    # The later night serves under the adopted lease, with its own SGLang container's name, then lets the lock go.
    served = run("serve", "sglang", AB_DIR=str(later), GPU_LOCK_WAIT="1")
    assert served.returncode == 0 and "already the night's" in served.stderr, served.stderr
    assert "rm -f ab-n15-sglang" in calls.read_text()
    assert run("restore", AB_DIR=str(later)).returncode == 0
    assert wait_until(lambda: lock_free(lock))


def test_adopt_refuses_without_a_live_lease_to_take(remote, tmp_path):
    run, _calls, _lock, earlier = remote
    later = tmp_path / "ab-n15"
    later.mkdir()
    refused = run("adopt", str(earlier), AB_DIR=str(later))
    assert refused.returncode == 1 and "no live lease" in refused.stderr
    assert not (later / "gpu-lease.pid").exists()


def disk_night(tmp_path, free_after_stop, **extra):
    """A night whose first round stops short once ("3 not run yet").

    A stand-in df says `free_after_stop` GB are left, and 500 GB once the next poll comes. Returns
    (result, recorded admin calls, D).
    """
    calls, state = tmp_path / "calls.txt", tmp_path / "state"
    state.mkdir()
    (state / "free").write_text(f"{free_after_stop}\n")
    fake = tmp_path / "python"
    fake.write_text(
        f'#!/bin/bash\nshift 2\necho "$*" >> "{calls}"\n'
        f'case "$*" in "swe-bench report n4-default") '
        f'[ -e "{state}/resumed" ] || {{ [ -e "{state}/stopped" ] && echo "INCOMPLETE          3 not run yet"; }} ;;\n'
        f'  "swe-bench run "*n4-default\\ *) [ -e "{state}/stopped" ] && touch "{state}/resumed";'
        f' touch "{state}/stopped" ;; esac\n'
    )
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
    env = {
        **os.environ,
        "WT": ROOT,
        "PY": str(fake),
        "LIST": str(task_list),
        "D": str(folder),
        "NIGHT": "4",
        "PREFIX": "n4",
        "ARMS": "default tests-v2",
        "REFINE": "0",
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DISK_POLL_S": "1",
        **extra,
    }
    result = subprocess.run(
        ["bash", SCRIPT, "run"], env=env, capture_output=True, text=True, timeout=60
    )
    return result, calls.read_text().splitlines(), folder


def test_a_round_stopped_for_disk_pauses_the_night_and_resumes_the_same_round(tmp_path):
    result, recorded, folder = disk_night(tmp_path, 90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [r.split("--name ")[1].split()[0] for r in runs(recorded)] == [
        "n4-default",
        "n4-default",
        "n4-tests-v2",
    ]
    assert (
        "paused: 90G free, the reserve is 100G" in result.stdout
        and "n4-default resumed: 500G free" in result.stdout
    )
    assert not (folder / "failed").exists()


def test_a_round_that_stops_short_with_the_disk_fine_still_fails_the_night(tmp_path):
    result, recorded, folder = disk_night(tmp_path, 300)
    assert result.returncode == 1 and (folder / "failed").exists()
    assert "instances not run" in (folder / "failed").read_text() and len(runs(recorded)) == 1


def test_a_disk_that_stays_short_fails_the_night_after_the_pause_bound(tmp_path):
    result, recorded, folder = disk_night(tmp_path, 90, DISK_PAUSE_MAX_S="0")
    assert result.returncode == 1 and (folder / "failed").exists()
    assert "the disk stayed short" in result.stdout and len(runs(recorded)) == 1


def test_every_round_and_regrade_takes_the_image_chunk_and_a_round_its_shard(tmp_path):
    status, recorded, _ = night(tmp_path, "default tests-v2")
    assert status == 0
    assert all(
        "--eval --remove-images --image-chunk 25 " in r and "--shard" not in r
        for r in runs(recorded)
    )
    sharded = tmp_path / "sharded"
    sharded.mkdir()
    status, recorded, _ = night(sharded, "default tests-v2", IMAGE_CHUNK="10", SHARD="2/3")
    assert status == 0
    assert all("--image-chunk 10 --shard 2/3 " in r for r in runs(recorded)), runs(recorded)
    assert (
        "swe-bench eval n4-tests-v2 --drop-test-hunks --remove-images --image-chunk 10" in recorded
    )


def test_start_needs_disk_for_one_chunk_of_the_largest_repository(tmp_path):
    tasks = [f"django__django-{n}" for n in range(86)] + ["sympy__sympy-1"]
    # 25 of django's 86 images at 3 GB, no reserve: 75 GB. 70 free is too little, 80 enough.
    result, recorded, _, units, folder = start(
        tmp_path, "default tests-v2", tasks=tasks, free_gb=70
    )
    assert result.returncode == 1 and runs(recorded) == [] and units == [] and not folder.exists()
    assert (
        "Tasks: 87; largest repository: 86, at most 25 of its images at once; disk needed about 75G, free 70G."
        in result.stdout
    )
    (tmp_path / "enough").mkdir()
    result, recorded, _, units, folder = start(
        tmp_path / "enough", "default tests-v2", tasks=tasks, free_gb=80
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "IMAGE_CHUNK=25" in units[0] and "SHARD=" in units[0]
    # The whole repository at once (IMAGE_CHUNK=0) needs 258 GB.
    (tmp_path / "whole").mkdir()
    result, _, _, _, _ = start(
        tmp_path / "whole", "default tests-v2", tasks=tasks, free_gb=200, IMAGE_CHUNK="0"
    )
    assert (
        result.returncode == 1
        and "at most 86 of its images at once; disk needed about 258G" in result.stdout
    )


def test_start_passes_the_shard_to_the_unit_but_never_to_a_smoke(tmp_path):
    result, recorded, _, units, _ = start(tmp_path, "default tests-v2", free_gb=500, SHARD="1/3")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(runs(recorded)) == 2 and not any("--shard" in r for r in runs(recorded))
    assert "SHARD=1/3" in units[0] and "(this machine: shard 1/3)" in result.stdout


@pytest.mark.parametrize(
    "extra",
    [{"SHARD": "3"}, {"SHARD": "0/3"}, {"SHARD": "4/3"}, {"SHARD": "a/b"}, {"IMAGE_CHUNK": "x"}],
)
def test_start_refuses_a_malformed_shard_or_chunk_before_anything_runs(tmp_path, extra):
    result, recorded, _, units, folder = start(tmp_path, "default tests-v2", free_gb=500, **extra)
    assert result.returncode == 2 and runs(recorded) == [] and units == [] and not folder.exists()


PINNED_REF = "0123456789abcdef0123456789abcdef01234567"


def test_a_pinned_series_builds_ling_once_records_it_and_never_rebuilds(tmp_path):
    import hashlib

    first = tmp_path / "first"
    first.mkdir()
    result, recorded, _, units, _ = start(
        first, "default tests-v2", free_gb=500, REF=PINNED_REF, PINS=str(tmp_path / "pins")
    )
    assert result.returncode == 0, result.stdout + result.stderr
    built = hashlib.sha256((first / "ling").read_bytes()).hexdigest()
    record = tmp_path / "pins" / f"ling-{PINNED_REF}.sha256"
    assert record.read_text().split()[0] == built and PINNED_REF in record.read_text()
    assert recorded.count("codex build") == 1 and f"LING_PIN_SHA256={built}" in units[0]
    # The pinned commit is here (git cat-file succeeds), so nothing is fetched.
    assert not any(
        line.startswith("-C") and " fetch " in f" {line} "
        for line in (first / "git.txt").read_text().splitlines()
    )
    # The next night of the series: the same build, found, not rebuilt.
    second = tmp_path / "second"
    second.mkdir()
    result, recorded, _, units, _ = start(
        second, "default tests-v2", free_gb=500, REF=PINNED_REF, PINS=str(tmp_path / "pins")
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "codex build" not in recorded and f"LING_PIN_SHA256={built}" in units[0]
    assert "no fetch past it, no rebuild" in result.stdout


def test_a_pinned_series_refuses_a_ling_that_is_not_its_build(tmp_path):
    pins = tmp_path / "pins"
    pins.mkdir()
    (pins / f"ling-{PINNED_REF}.sha256").write_text(
        f"{'0' * 64}  /somewhere/ling  {PINNED_REF}  2026-10-20\n"
    )
    result, recorded, _, units, folder = start(
        tmp_path, "default tests-v2", free_gb=500, REF=PINNED_REF, PINS=str(pins)
    )
    assert result.returncode == 1 and "is not the build pinned for" in result.stdout
    assert (
        "codex build" not in recorded
        and runs(recorded) == []
        and units == []
        and not folder.exists()
    )


def test_an_unpinned_ref_neither_builds_nor_records(tmp_path):
    result, recorded, _, units, _ = start(
        tmp_path, "default tests-v2", free_gb=500, PINS=str(tmp_path / "pins")
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "codex build" not in recorded and not (tmp_path / "pins").exists()
    assert "--setenv=LING_PIN_SHA256= " in units[0]
    assert any(" fetch " in f" {line} " for line in (tmp_path / "git.txt").read_text().splitlines())


def test_each_round_of_a_pinned_night_checks_the_build(tmp_path):
    import hashlib

    ling = tmp_path / "ling"
    ling.write_bytes(b"the pinned build")
    pin = hashlib.sha256(ling.read_bytes()).hexdigest()
    status, recorded, folder = night(tmp_path, "default tests-v2", ling=ling, LING_PIN_SHA256=pin)
    assert status == 0 and len(runs(recorded)) == 2 and (folder / "done").exists()
    other = tmp_path / "other"
    other.mkdir()
    status, recorded, folder = night(other, "default tests-v2", ling=ling, LING_PIN_SHA256="f" * 64)
    assert (
        status == 1
        and runs(recorded) == []
        and "pinned ling check" in (folder / "failed").read_text()
    )


def test_a_model_server_arm_with_no_remote_machine_named_ends_the_night_before_its_round(tmp_path):
    status, recorded, folder = night(tmp_path, "default+on-sglang default+on-engine", REMOTE="")
    assert status == 1 and not runs(recorded) and (folder / "failed").exists()


TARGET = "/sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/serving_responses.py"


def patched_sglang(tmp_path, ab_dir, volume=True):
    """Night 17's folder: a runtime patch listed in sglang-patches/mounts.

    Its serve.py records its arguments, and takes `--volume` when `volume`.
    """
    patches = ab_dir / "sglang-patches"
    patches.mkdir()
    (patches / "serving_responses.py-ed490a69c960-f747c48de5d4").write_text("# patched\n")
    (patches / "mounts").write_text(f"serving_responses.py-ed490a69c960-f747c48de5d4 {TARGET}\n")
    record = tmp_path / "serve-args.txt"
    flag = '"--volume"' if volume else '"--nvtx-patch"'
    (ab_dir / "serve.py").write_text(
        f"import sys\n# argparse: {flag}\nopen({str(record)!r}, 'w').write(' '.join(sys.argv[1:]))\n"
    )
    return record


def probe(tmp_path, answer):
    script = tmp_path / "probe.sh"
    script.write_text(f"#!/bin/bash\necho '{answer}'\n")
    script.chmod(0o755)
    return str(script)


def test_the_sglang_arm_mounts_the_runtime_patch_and_proves_the_phase_merge_before_it_is_ready(
    remote, tmp_path
):
    run, _calls, _lock, ab_dir = remote
    record = patched_sglang(tmp_path, ab_dir)
    (tmp_path / "state" / "mounts").write_text(f"/models {TARGET} ")
    served = run("serve", "sglang", PHASE_PROBE_CMD=probe(tmp_path, "same 342"))
    assert served.returncode == 0 and "ready sglang" in served.stdout, served.stderr
    assert (
        f"--volume {ab_dir}/sglang-patches/serving_responses.py-ed490a69c960-f747c48de5d4:{TARGET}:ro"
        in (record.read_text())
    )
    assert "phase merge in force: 342 prompt tokens" in served.stderr


def test_a_phase_merge_not_in_force_stops_the_sglang_arm(remote, tmp_path):
    run, calls, _lock, ab_dir = remote
    patched_sglang(tmp_path, ab_dir)
    (tmp_path / "state" / "mounts").write_text(f"{TARGET} ")
    served = run("serve", "sglang", PHASE_PROBE_CMD=probe(tmp_path, "differ 350 342"))
    assert served.returncode == 1 and "phase merge NOT in force (differ 350 342)" in served.stderr
    assert "rm -f ab-n12-sglang" in calls.read_text()


def test_a_patch_that_is_not_mounted_stops_the_sglang_arm_before_any_probe(remote, tmp_path):
    run, _calls, _lock, ab_dir = remote
    patched_sglang(tmp_path, ab_dir)
    served = run("serve", "sglang", PHASE_PROBE_CMD=probe(tmp_path, "same 342"))
    assert served.returncode == 1 and "the phase-merge patch is not mounted" in served.stderr


def test_runtime_patches_need_a_serve_py_that_mounts_them(remote, tmp_path):
    run, _calls, _lock, ab_dir = remote
    patched_sglang(tmp_path, ab_dir, volume=False)
    served = run("serve", "sglang", PHASE_PROBE_CMD=probe(tmp_path, "same 342"))
    assert served.returncode == 1 and "has no --volume for the runtime patches" in served.stderr


def test_without_runtime_patches_the_sglang_arm_starts_as_before(remote, tmp_path):
    run, _calls, _lock, _ab_dir = remote
    served = run("serve", "sglang")
    assert served.returncode == 0 and "phase merge" not in served.stderr


def test_the_disk_reserve_reaches_the_unit_so_its_disk_pause_matches_the_runner(tmp_path):
    # A night started with RESERVE_GB=150 writes that reserve for the runner; the unit's disk pause must
    # wait for the same reserve, not the default 100.
    with pathlib.Path(SCRIPT).open() as script:
        body = script.read()
    assert '--setenv=RESERVE_GB="$RESERVE_GB"' in body
    result, _recorded, _folder = disk_night(tmp_path, 140, RESERVE_GB="150")
    assert result.returncode == 0 and "paused: 140G free, the reserve is 150G" in result.stdout


@pytest.mark.parametrize("caller", ["ling-swe-chain14", "ling-swe-chain14.service"])
def test_a_pinned_series_builds_from_its_own_chains_unit(tmp_path, caller):
    # A chain arms its night from its own ling-swe-* unit (night 17's chain was refused by its own name on
    # 2026-10-26): the unit that called `start` does not count as a night the build would disturb.
    result, recorded, _, units, _ = start(
        tmp_path,
        "default tests-v2",
        free_gb=500,
        REF=PINNED_REF,
        PINS=str(tmp_path / "pins"),
        ACTIVE_UNITS="ling-swe-chain14.service",
        CALLER_UNIT=caller,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert recorded.count("codex build") == 1 and units


def test_a_pinned_series_still_waits_for_another_night_that_runs(tmp_path):
    result, recorded, _, units, folder = start(
        tmp_path,
        "default tests-v2",
        free_gb=500,
        REF=PINNED_REF,
        PINS=str(tmp_path / "pins"),
        ACTIVE_UNITS="ling-swe-chain14.service ling-swe-night13.service",
        CALLER_UNIT="ling-swe-chain14",
    )
    assert result.returncode == 1
    assert "Not building the pinned ling while ling-swe-night13.service" in result.stdout
    assert "ling-swe-chain14" not in result.stdout.split("while", 1)[1].split("runs", 1)[0]
    assert "codex build" not in recorded and units == [] and not folder.exists()


def test_a_night_that_wins_ignores_open_sessions_in_every_round(tmp_path):
    status, recorded, _ = night(tmp_path, "default tests-v2", NIGHT_WINS="1")
    assert status == 0 and runs(recorded)
    assert all("--ignore-open-sessions" in r for r in runs(recorded))
    quiet = tmp_path / "quiet"
    quiet.mkdir()
    status, recorded, _ = night(quiet, "default tests-v2")
    assert status == 0 and not any("--ignore-open-sessions" in r for r in runs(recorded))


def test_a_night_that_wins_waits_out_an_index_run_then_stops_it(tmp_path):
    scope = "mightling-index-docs-downloads-e1-1.scope"
    status, recorded, _ = night(
        tmp_path, "default tests-v2", NIGHT_WINS="1", INDEX_SCOPES=scope, INDEX_WAIT_S="0"
    )
    assert status == 0 and len(runs(recorded)) == 2
    assert f"--user stop {scope}" in (tmp_path / "systemctl.txt").read_text()


def test_a_night_by_hand_leaves_index_runs_to_the_runner(tmp_path):
    scope = "mightling-index-docs-downloads-e1-1.scope"
    status, _, _ = night(tmp_path, "default tests-v2", INDEX_SCOPES=scope, INDEX_WAIT_S="0")
    assert status == 0 and not (tmp_path / "systemctl.txt").exists()


def test_the_image_cache_reaches_the_nights_and_the_smokes_settings(tmp_path):
    with pathlib.Path(SCRIPT).open() as script:
        body = script.read()
    assert body.count("cache_toml; }") == 2
    result = subprocess.run(
        ["bash", "-c", f'source <(sed -n "/^cache_toml()/,/^}}/p" {SCRIPT}); cache_toml'],
        env={**os.environ, "IMAGE_CACHE_LISTS": "/l/a.txt /l/b.txt", "IMAGE_CACHE_FLOOR": "175G"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert (
        result.stdout
        == 'image_cache_lists = ["/l/a.txt", "/l/b.txt"]\nimage_cache_floor = "175G"\n'
    )


def test_the_sampling_arm_reaches_only_the_candidates_round(tmp_path):
    status, recorded, _ = night(tmp_path, "default default+sampling-nonthinking")
    assert status == 0
    record, candidate = runs(recorded)
    assert "--sampling" not in record and "--sampling nonthinking" in candidate
    assert "--name n4-default-sampling-nonthinking " in candidate


@pytest.mark.parametrize("arm", ["sampling-thinking", "sampling", "sampling_nonthinking"])
def test_a_misspelt_sampling_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default default+{arm}")
    assert status == 2 and not runs(recorded) and (folder / "failed").exists()


def test_the_compat_arm_reaches_only_the_candidates_round(tmp_path):
    status, recorded, _ = night(tmp_path, "default default+compat-claude")
    assert status == 0
    record, candidate = runs(recorded)
    assert "--compat" not in record and "--compat claude" in candidate


@pytest.mark.parametrize("arm", ["compat-codex", "compat", "compat_claude"])
def test_a_misspelt_compat_arm_ends_the_night_before_any_round(tmp_path, arm):
    status, recorded, folder = night(tmp_path, f"default default+{arm}")
    assert status == 2 and not runs(recorded) and (folder / "failed").exists()


def test_a_one_arm_multilingual_night_runs_one_round_its_regrade_and_its_report(tmp_path):
    status, recorded, folder = night(tmp_path, "default", DATASET="multilingual")
    assert status == 0 and (folder / "done").exists()
    (round_,) = runs(recorded)
    assert "--dataset multilingual " in round_ and "--name n4-default " in round_
    assert (
        "swe-bench eval n4-default --drop-test-hunks --remove-images --image-chunk 25" in recorded
    )
    assert (folder / "report-default.txt").exists()
    assert not any(
        "--against n4-default" in line and "report n4-default " not in line for line in recorded
    )
    assert not list(folder.glob("*-against-*.txt"))


def test_a_night_runs_verified_unless_told_otherwise(tmp_path):
    status, recorded, _ = night(tmp_path, "default tests-v2")
    assert status == 0
    assert all("--dataset verified " in line for line in runs(recorded))


def test_three_arms_end_the_night_before_any_round(tmp_path):
    status, recorded, folder = night(tmp_path, "default tests-v2 issue-v1")
    assert status == 2 and runs(recorded) == [] and (folder / "failed").exists()


@pytest.mark.parametrize(
    "production", [{}, {"LOCAL_PROD_STOP": "m0-prod8000", "LOCAL_RESTORE_ENGINE": "none"}]
)
def test_local_arms_serve_each_arm_on_this_machine_and_bring_production_back(tmp_path, production):
    # LOCAL_ARMS=1 (the 1.8.0 bar night): the same on-sglang/on-engine arms, served by the local helper (a
    # stand-in here, recording into the night's call log) instead of over ssh; never the remote helper.
    calls = tmp_path / "calls.txt"
    helper = tmp_path / "local_arm.sh"
    helper.write_text(
        "#!/bin/bash\n"
        f'echo "helper $* [$PROD_STOP|$RESTORE_ENGINE|$DREAMFERENCE_VLLM_HOST]" >> "{calls}"\n'
        'case "$1" in serve) echo "ready $2 image-of-$2" ;; restore) echo restored ;; esac\n'
    )
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    stand_ins = tmp_path / "stand-ins"
    stand_ins.mkdir()
    ssh = stand_ins / "ssh"
    ssh.write_text(f'#!/bin/bash\necho "ssh $*" >> "{calls}"\nexit 1\n')
    ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)
    status, recorded, folder = night(
        tmp_path,
        "default+on-sglang default+on-engine",
        LOCAL_ARMS="1",
        LOCAL_ARM_HELPER=str(helper),
        REMOTE="",
        **production,
    )
    assert status == 0 and (folder / "done").exists(), recorded
    assert not any(line.startswith("ssh ") for line in recorded)
    order = [
        line.split(" [")[0] if line.startswith("helper ") else line.split(" --")[0]
        for line in recorded
        if line.startswith(("helper ", "swe-bench run ", "swe-bench eval "))
    ]
    assert order == [
        "helper serve sglang",
        "swe-bench run",
        "helper serve engine",
        "swe-bench run",
        # Production back once both rounds have run, before the regrades; the EXIT trap's is cleared.
        "helper restore",
        "swe-bench eval n4-default-on-sglang",
        "swe-bench eval n4-default-on-engine",
    ]
    settings = f"[{production.get('LOCAL_PROD_STOP', '')}|{production.get('LOCAL_RESTORE_ENGINE', 'sglang')}|]"
    assert all(line.endswith(settings) for line in recorded if line.startswith("helper "))
    with pathlib.Path(SCRIPT).open() as script:
        body = script.read()
    assert '--setenv=LOCAL_ARMS="$LOCAL_ARMS"' in body
    assert '--setenv=LOCAL_PROD_STOP="$LOCAL_PROD_STOP"' in body
    assert '[ "$LOCAL_ARMS" = 1 ] && stop_timeout=(-p TimeoutStopSec=1h)' in body
    assert '-p OOMPolicy=continue "${stop_timeout[@]}"' in body


def test_without_local_arms_an_engine_arm_still_needs_its_remote_machine(tmp_path):
    status, recorded, folder = night(
        tmp_path, "default+on-sglang default+on-engine", LOCAL_ARMS="0", REMOTE=""
    )
    assert status == 1 and not runs(recorded) and (folder / "failed").exists()


def test_local_arms_pass_each_arms_engine_flags_and_keep_its_step_trace(tmp_path):
    # The fair-pairing night's shape: the same engine twice, one flag apart; each engine round writes a step
    # trace, kept in the night's folder once production is back.
    calls, traces = tmp_path / "calls.txt", tmp_path / "traces"
    helper = tmp_path / "local_arm.sh"
    helper.write_text(
        "#!/bin/bash\n"
        f'echo "helper $* [$ENGINE_ARGS|$ENGINE_ENV]" >> "{calls}"\n'
        'case "$1" in serve) echo "ready $2 image"\n'
        '    t="${ENGINE_ENV#LING_STEP_TRACE=/root/.cache/dreamference/night-traces/}"\n'
        f'    [ -n "$ENGINE_ENV" ] && echo "{{}}" > "{traces}/$t" ;;\n'
        "restore) echo restored ;; esac\n"
    )
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    status, recorded, folder = night(
        tmp_path,
        "default+on-engine+engine-fair-pairing-0 default+on-engine+engine-fair-pairing-1",
        LOCAL_ARMS="1",
        LOCAL_ARM_HELPER=str(helper),
        LOCAL_STEP_TRACE="1",
        TRACE_HOST=str(traces),
    )
    assert status == 0 and (folder / "done").exists(), recorded
    served = [line for line in recorded if line.startswith("helper serve")]
    trace = "LING_STEP_TRACE=/root/.cache/dreamference/night-traces"
    assert served == [
        f"helper serve engine [--fair-pairing 0|{trace}/n4-default-on-engine-engine-fair-pairing-0.jsonl]",
        f"helper serve engine [--fair-pairing 1|{trace}/n4-default-on-engine-engine-fair-pairing-1.jsonl]",
    ]
    assert all("--fair-pairing" not in line for line in runs(recorded))
    kept = sorted(path.name for path in (folder / "step-trace").iterdir())
    assert kept == [
        "n4-default-on-engine-engine-fair-pairing-0.jsonl",
        "n4-default-on-engine-engine-fair-pairing-1.jsonl",
    ]


def test_engine_flags_without_local_arms_end_the_night_before_any_round(tmp_path):
    status, recorded, folder = night(
        tmp_path, "default+on-engine+engine-fair-pairing-1 default+on-sglang", REMOTE="node-b"
    )
    assert status == 2 and not runs(recorded) and (folder / "failed").exists()


def test_a_misspelt_engine_flag_ends_the_night_before_any_round(tmp_path):
    status, recorded, _ = night(tmp_path, "default+on-engine+engine-fair default", LOCAL_ARMS="1")
    assert status == 2 and not runs(recorded)


def test_a_local_arms_night_unit_gets_an_hour_to_stop_and_others_the_default(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    result, _, _, units, _ = start(local, "default tests-v2", free_gb=500, LOCAL_ARMS="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert " -p TimeoutStopSec=1h " in f" {units[0]} "
    plain = tmp_path / "plain"
    plain.mkdir()
    result, _, _, units, _ = start(plain, "default tests-v2", free_gb=500)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TimeoutStopSec" not in units[0]


def test_start_refuses_a_worktree_without_the_drop_flag(tmp_path):
    # A worktree pinned before --drop-old-outputs: start stops before it looks at the installed ling.
    worktree = tmp_path / "wt"
    shutil.copytree(pathlib.Path(ROOT) / "dreamference" / "swe_bench", worktree / "dreamference" / "swe_bench")
    command = worktree / "dreamference" / "swe_bench" / "swe_bench_command.py"
    command.write_text(command.read_text().replace('"--drop-old-outputs"', '"--other-flag"'))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("systemctl", "exit 1"), ("git", "exit 0")):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/bash\n{body}\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "WT": str(worktree),
        "PY": "false",
        "D": str(tmp_path / "night7"),
        "NIGHT": "7",
        "UNIT": "ling-swe-night7-test",
        "LIVE_UNIT": "ling-swe-night6-test",
        "ARMS": "default drop-outputs",
        "LIST": str(tmp_path / "list.txt"),
    }
    result = subprocess.run(
        ["bash", SCRIPT, "start"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 1
    assert f"{worktree} does not have --drop-old-outputs: pin REF to a commit that has it." in result.stdout
    assert not (tmp_path / "night7").exists()
