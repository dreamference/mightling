"""Night Shift's runner (specs/DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md §5, §8).

A scripted stand-in for `puffin` plays the agent: it changes files, stalls, stalls and then acts
after a nudge, hangs, or fails. Nothing here starts a systemd scope or unit, talks to the model
server or opens the user's queue: every test has its own queue directory and repository.
"""

import json
import os
import stat
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dreamference.night_shift import (
    NightShiftHost, NightShiftIndex, NightShiftQueue, NightShiftReport, NightShiftRunner, NightShiftScheduler,
    NightShiftSettings, NightShiftTaskRun,
)
from dreamference.night_shift.night_shift_task_run import NUDGE

FAKE_PUFFIN = textwrap.dedent("""\
    #!{python}
    # A stand-in for `puffin exec`: behaviour from $FAKE_PUFFIN_MODE, every call logged.
    # `puffin sandbox … -- <command>` is logged apart, with what the runner set for it, and the
    # command is run as it is: the suite never starts the real sandbox.
    import json, os, sys, time, uuid
    args = sys.argv[1:]
    if args[0] == "sandbox":
        with open(os.environ["FAKE_PUFFIN_CALLS"] + ".sandbox", "a") as log:
            seen = {{name: os.environ.get(name) for name in ("CODEX_THREAD_ID", "DREAMFERENCE_PUFFIN_AIRGAPPED")}}
            log.write(json.dumps({{"args": args, "env": seen, "cwd": os.getcwd()}}) + "\\n")
        command = args[args.index("--") + 1:]
        os.execvp(command[0], command)
    with open(os.environ["FAKE_PUFFIN_CALLS"], "a") as log:
        log.write(json.dumps(args) + "\\n")
    cwd = args[args.index("-C") + 1]
    out = args[args.index("-o") + 1]
    resume = "resume" in args
    session = args[args.index("resume") + 1] if resume else str(uuid.uuid4())
    prompt = args[-1]
    mode = os.environ.get("FAKE_PUFFIN_MODE", "change")
    print(json.dumps({{"type": "thread.started", "thread_id": session}}), flush=True)
    def say(text):
        with open(out, "w") as handle:
            handle.write(text)
    if mode == "hang":
        time.sleep(600)
    if mode == "error":
        sys.exit(1)
    if mode == "loosen":
        with open(os.path.join(cwd, "dreamference.toml"), "w") as handle:
            handle.write('puffin_airgapped = "off"\\n')
        say("Loosened the level.")
    acts = mode == "change" or (mode == "stall_then_act" and prompt == {nudge!r}) \\
        or (mode == "act_on_resume" and resume)
    if acts:
        with open(os.path.join(cwd, "hello.txt"), "w") as handle:
            handle.write("hi\\n")
        say("Created hello.txt.\\n\\nFiles changed: hello.txt")
    elif mode in ("stall", "stall_then_act"):
        say("I'll now create hello.txt with the greeting.")
    else:
        say("Nothing needed changing.")
    print(json.dumps({{"type": "turn.completed"}}), flush=True)
""")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A repository with one commit, a queue directory and the fake `puffin`."""
    monkeypatch.setattr(NightShiftTaskRun, "USE_SCOPE", False)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "night@test")
    git(repo, "config", "user.name", "Night Test")
    (repo / "README.md").write_text("readme\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    puffin = tmp_path / "puffin"
    puffin.write_text(FAKE_PUFFIN.format(python=sys.executable, nudge=NUDGE))
    puffin.chmod(puffin.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_PUFFIN_CALLS", str(calls))
    night = tmp_path / "night"
    return {"repo": repo, "night": night, "puffin": str(puffin), "calls": calls}


def queue(night: Path, repo: Path, task_text: str = "Add hello.txt", test=None, task_id="20261001-0100-abc") -> dict:
    """Writes a task as the launcher does (puffin-rs/src/night.rs)."""
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    record = {
        "id": task_id, "repo": str(repo), "base": base, "branch": f"night/{task_id}", "task": task_text,
        "test": test, "model_at_add": "test-model", "status": "queued",
        "history": [{"at": "2026-10-01T21:00:00+01:00", "status": "queued"}],
        "attempts": 0, "nudges": 0, "session": None, "result": None,
    }
    (night / "tasks").mkdir(parents=True, exist_ok=True)
    (night / "tasks" / f"{task_id}.json").write_text(json.dumps(record))
    return record


def run_task(setup, monkeypatch, mode, deadline_s=60, **queue_args):
    monkeypatch.setenv("FAKE_PUFFIN_MODE", mode)
    record = queue(setup["night"], setup["repo"], **queue_args)
    run = NightShiftTaskRun(setup["night"], record, NightShiftSettings({}), setup["puffin"],
                            deadline=time.time() + deadline_s)
    status = run.run()
    return status, NightShiftQueue.read(setup["night"], record["id"]), run


def calls(setup):
    return [json.loads(line) for line in setup["calls"].read_text().splitlines()]


def sandbox_calls(setup):
    """The test runs the runner sent through `puffin sandbox`, with their environment."""
    log = Path(str(setup["calls"]) + ".sandbox")
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# -- one task -------------------------------------------------------------------------------------

def test_a_change_is_committed_on_its_branch_and_the_checkout_is_untouched(setup, monkeypatch):
    head = git(setup["repo"], "rev-parse", "HEAD").stdout
    status, record, run = run_task(setup, monkeypatch, "change", test="test -f hello.txt")
    assert status == "done"
    assert record["result"]["test_result"] == "passed"
    assert record["result"]["test_source"] == "--test"
    assert "1 file changed" in record["result"]["diff_stat"]
    log = git(setup["repo"], "log", "-1", "--format=%s%n%an", "night/20261001-0100-abc").stdout
    assert log.splitlines() == ["night: Add hello.txt", "Night Test"]
    # The user's checkout, branch and HEAD are as they were; the worktree is gone.
    assert git(setup["repo"], "rev-parse", "HEAD").stdout == head
    assert git(setup["repo"], "status", "--porcelain").stdout == ""
    assert git(setup["repo"], "branch", "--show-current").stdout.strip() in ("master", "main")
    assert not run.worktree.exists()
    # The agent ran unattended, in the worktree, under the workspace-write sandbox.
    first = calls(setup)[0]
    assert first[:2] == ["exec", "--json"] and "workspace-write" in first
    assert first[first.index("-C") + 1] == str(run.worktree)
    assert "Do not commit, push" in first[-1] and first[-1].endswith("Add hello.txt")
    assert "run `test -f hello.txt`" in first[-1]
    assert record["session"]


def test_what_the_test_run_leaves_behind_is_not_committed(setup, monkeypatch):
    # The live run on 2026-10-01 committed pytest's `__pycache__` in a repository with no .gitignore.
    status, record, _ = run_task(setup, monkeypatch, "change", test="echo junk > leftover.pyc")
    assert status == "done"
    files = git(setup["repo"], "show", "--name-only", "--format=", "night/20261001-0100-abc").stdout
    assert files.split() == ["hello.txt"]


def test_the_test_run_is_sandboxed_with_a_policy_the_runner_fixes(setup, monkeypatch):
    # The test command runs what the agent wrote. Until 2026-10-02 it ran as plain `bash -c`, with
    # the user's full rights; it now goes through `puffin sandbox`, the agent's own sandbox.
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    status, record, run = run_task(setup, monkeypatch, "change", test="test -f hello.txt")
    assert status == "done" and record["result"]["test_result"] == "passed"
    assert record["result"]["test_sandbox"] == "workspace-write"
    (call,) = sandbox_calls(setup)
    args = call["args"]
    assert args[:2] == ["sandbox", "-c"] and args[-3:] == ["bash", "-c", "test -f hello.txt"]
    options = [args[index + 1] for index, arg in enumerate(args) if arg == "-c" and index < args.index("--")]
    # Workspace-write, and nothing from the user's config: `~/.puffin/skills` is writable there, and
    # a test run that could write it could leave instructions for every later session.
    assert options == ['sandbox_mode="workspace-write"', "sandbox_workspace_write.writable_roots=[]",
                       "sandbox_workspace_write.network_access=true"]
    assert call["cwd"] == str(run.worktree)
    # The sandbox helper looks the /airgapped level up by the task's own session.
    # ...and the runner hands every command of the task the level it fixed before the first one.
    assert call["env"] == {"CODEX_THREAD_ID": record["session"], "DREAMFERENCE_PUFFIN_AIRGAPPED": "off"}
    assert record["airgapped"] == "off"
    assert "sandbox: workspace-write" in NightShiftReport.render(datetime.now().astimezone(), [record], [])


def test_at_airgapped_on_the_test_run_has_no_network(setup, monkeypatch):
    # The worktree's own config file may tighten the level (the agent can write it), never loosen it.
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    (setup["repo"] / "dreamference.toml").write_text('puffin_airgapped = "on"\n')
    git(setup["repo"], "add", "-A")
    git(setup["repo"], "commit", "-q", "-m", "seal")
    status, record, _ = run_task(setup, monkeypatch, "change", test="true")
    assert status == "done"
    (call,) = sandbox_calls(setup)
    assert "sandbox_workspace_write.network_access=false" in call["args"]
    assert call["env"]["DREAMFERENCE_PUFFIN_AIRGAPPED"] == "on"
    assert record["result"]["test_sandbox"] == "workspace-write, no network (/airgapped on)"


def test_the_agent_cannot_loosen_the_level_for_its_own_tests(setup, monkeypatch):
    # The level is fixed before the agent runs. Read again before the test run, it would be whatever
    # the agent had by then written into the worktree's dreamference.toml.
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    (setup["repo"] / "dreamference.toml").write_text('puffin_airgapped = "on"\n')
    git(setup["repo"], "add", "-A")
    git(setup["repo"], "commit", "-q", "-m", "seal")
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "loosen")
    record = queue(setup["night"], setup["repo"], test="true")
    run = NightShiftTaskRun(setup["night"], record, NightShiftSettings({}), setup["puffin"], time.time() + 60)
    assert run.run() == "done"
    assert (run.repo / "dreamference.toml").exists()
    changed = git(setup["repo"], "show", "--format=", record["branch"]).stdout
    assert '+puffin_airgapped = "off"' in changed
    (call,) = sandbox_calls(setup)
    assert "sandbox_workspace_write.network_access=false" in call["args"]
    assert call["env"]["DREAMFERENCE_PUFFIN_AIRGAPPED"] == "on"
    # A task cut off and resumed another night keeps at least the level its first night recorded.
    resumed = dict(NightShiftQueue.read(setup["night"], record["id"]), id="20261001-0100-abe",
                   branch="night/20261001-0100-abe")
    (setup["night"] / "tasks" / "20261001-0100-abe.json").write_text(json.dumps(resumed))
    again = NightShiftTaskRun(setup["night"], resumed, NightShiftSettings({}), setup["puffin"], time.time() + 60)
    (setup["repo"] / "dreamference.toml").write_text('puffin_airgapped = "off"\n')
    again.worktree.mkdir(parents=True)
    assert again._fix_level() == "on"


def test_night_airgapped_tightens_the_level_and_never_loosens_it(setup, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    record = queue(setup["night"], setup["repo"])
    level = lambda table: NightShiftTaskRun(setup["night"], record, NightShiftSettings(table), setup["puffin"],
                                            time.time() + 60)._fix_level()
    assert level({}) == "off"
    assert level({"airgapped": "on"}) == "on"
    assert level({"airgapped": "nonsense"}) == "off"
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "on")
    assert level({"airgapped": "off"}) == "on"


def test_the_main_checkouts_untracked_level_reaches_its_night_tasks(setup, monkeypatch):
    # dreamference.toml is usually untracked, so the worktree has no copy of it.
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    (setup["repo"] / ".gitignore").write_text("dreamference.toml\n")
    git(setup["repo"], "add", "-A")
    git(setup["repo"], "commit", "-q", "-m", "ignore the local config")
    (setup["repo"] / "dreamference.toml").write_text('puffin_airgapped = "on"\n')
    status, record, _ = run_task(setup, monkeypatch, "change", test="true")
    assert status == "done" and record["airgapped"] == "on"
    (call,) = sandbox_calls(setup)
    assert "sandbox_workspace_write.network_access=false" in call["args"]


def test_the_airgapped_level_resolves_as_the_launcher_does(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    monkeypatch.delenv("DREAMFERENCE_CONFIG_PATH", raising=False)
    worktree, home = tmp_path / "tree", tmp_path / "puffin-home"
    worktree.mkdir()
    level = lambda session=None: NightShiftTaskRun.airgapped_level(worktree, session, home)
    assert level() == "off"
    user = Path(os.path.expanduser("~/.config/dreamference/config.toml"))
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text('puffin_airgapped = "duckduckgo"\n')
    assert level() == "duckduckgo"
    # Strictest of the files: the worktree's can tighten the user's, and cannot loosen it.
    (worktree / "dreamference.toml").write_text('puffin_airgapped = "on"  # sealed\n[night]\ntest = "x"\n')
    assert level() == "on"
    (worktree / "dreamference.toml").write_text('puffin_airgapped = "off"\n')
    assert level() == "duckduckgo"
    # A key under a table is not the top-level key.
    user.write_text('[night]\npuffin_airgapped = "on"\n')
    assert level() == "off"
    # The environment overrides the files, and the session's own file overrides both.
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "ON")
    assert level() == "on"
    (home / "airgapped").mkdir(parents=True)
    (home / "airgapped" / "0a1b-2c3d").write_text("ddg\n")
    assert level("0a1b-2c3d") == "duckduckgo"
    assert level("../escape") == "on"


def test_unsandboxed_tests_are_an_explicit_choice_and_refused_at_airgapped_on(setup, monkeypatch):
    monkeypatch.delenv("DREAMFERENCE_PUFFIN_AIRGAPPED", raising=False)
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "change")
    record = queue(setup["night"], setup["repo"], test="test -f hello.txt")
    settings = NightShiftSettings({"test_sandbox": False})
    run = NightShiftTaskRun(setup["night"], record, settings, setup["puffin"], time.time() + 60)
    assert run.run() == "done"
    result = NightShiftQueue.read(setup["night"], record["id"])["result"]
    assert result["test_result"] == "passed" and result["test_sandbox"] == "off ([night] test_sandbox = false)"
    assert sandbox_calls(setup) == []
    # With no sandbox nothing would keep the tests off the network, so at `on` they do not run.
    monkeypatch.setenv("DREAMFERENCE_PUFFIN_AIRGAPPED", "on")
    record = queue(setup["night"], setup["repo"], test="touch ran.txt", task_id="20261001-0100-abd")
    run = NightShiftTaskRun(setup["night"], record, settings, setup["puffin"], time.time() + 60)
    assert run.run() == "done"
    result = NightShiftQueue.read(setup["night"], record["id"])["result"]
    assert result["test_result"] == "untested" and "not run: /airgapped is on" in result["test_source"]
    assert "ran.txt" not in git(setup["repo"], "show", "--name-only", "--format=", record["branch"]).stdout


def test_a_failing_test_is_recorded_not_hidden(setup, monkeypatch):
    status, record, _ = run_task(setup, monkeypatch, "change", test="echo boom; exit 3")
    assert status == "done"
    assert record["result"]["test_result"] == "failed (exit 3)"
    assert "boom" in record["result"]["test_tail"]


def test_an_announce_only_reply_is_nudged_then_marked_stalled(setup, monkeypatch):
    status, record, run = run_task(setup, monkeypatch, "stall")
    assert status == "stalled"
    resumes = [call for call in calls(setup) if "resume" in call]
    assert len(resumes) == 2 and all(call[-1] == NUDGE for call in resumes)
    assert resumes[0][resumes[0].index("resume") + 1] == record["session"]
    assert record["result"]["nudges"] == 2
    assert "I'll now create" in record["result"]["last_message"]
    # No change, so no branch is kept; the log is.
    assert git(setup["repo"], "branch", "--list", "night/*").stdout == ""
    assert not run.worktree.exists()
    assert Path(record["result"]["log"]).exists()


def test_a_nudge_that_works_ends_in_done(setup, monkeypatch):
    status, record, _ = run_task(setup, monkeypatch, "stall_then_act")
    assert status == "done"
    assert record["result"]["nudges"] == 1


def test_no_change_and_no_announcement_is_no_change(setup, monkeypatch):
    status, record, _ = run_task(setup, monkeypatch, "nothing")
    assert status == "no-change"
    assert len(calls(setup)) == 1
    assert git(setup["repo"], "branch", "--list", "night/*").stdout == ""


def test_an_exec_error_with_no_change_is_failed(setup, monkeypatch):
    status, record, _ = run_task(setup, monkeypatch, "error")
    assert status == "failed"
    assert record["result"]["last_message"]


def test_a_task_cut_off_keeps_its_worktree_and_resumes_its_session_next_time(setup, monkeypatch):
    status, record, run = run_task(setup, monkeypatch, "hang", deadline_s=3)
    assert status == "interrupted"
    assert run.worktree.is_dir()
    session = record["session"]
    assert session and record["result"]["branch"] == "night/20261001-0100-abc"
    # The next night: the same worktree, the recorded session, and it finishes.
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "act_on_resume")
    rerun = NightShiftTaskRun(setup["night"], record, NightShiftSettings({}), setup["puffin"],
                              deadline=time.time() + 60)
    assert rerun.run() == "done"
    last = calls(setup)[-1]
    assert last[last.index("resume") + 1] == session
    assert NightShiftQueue.read(setup["night"], record["id"])["attempts"] == 2


def test_a_dropped_task_is_cancelled_before_it_starts(setup, monkeypatch):
    record = queue(setup["night"], setup["repo"])
    NightShiftQueue.update(setup["night"], record["id"],
                           lambda task: NightShiftQueue.set_status(task, "cancel-requested"))
    run = NightShiftTaskRun(setup["night"], record, NightShiftSettings({}), setup["puffin"], time.time() + 60)
    assert run.run() == "cancelled"
    assert not setup["calls"].exists()


def test_a_cancel_requested_while_running_wins_over_the_runners_next_status(setup):
    record = queue(setup["night"], setup["repo"])
    NightShiftQueue.transition(setup["night"], record["id"], "running")
    NightShiftQueue.update(setup["night"], record["id"],
                           lambda task: NightShiftQueue.set_status(task, "cancel-requested"))
    assert NightShiftQueue.transition(setup["night"], record["id"], "done") == "cancelled"


def test_a_vanished_base_commit_fails_the_task(setup, monkeypatch):
    record = queue(setup["night"], setup["repo"])
    record["base"] = "0" * 40
    run = NightShiftTaskRun(setup["night"], record, NightShiftSettings({}), setup["puffin"], time.time() + 60)
    assert run.run() == "failed"
    assert "no longer exists" in NightShiftQueue.read(setup["night"], record["id"])["result"]["last_message"]


# -- test detection ------------------------------------------------------------------------------

def test_test_command_detection_order(tmp_path):
    repo, tree = tmp_path / "repo", tmp_path / "tree"
    tree.mkdir()
    detect = lambda override=None, configured=None: NightShiftTaskRun.detect_test_command(
        tree, override, configured, repo)
    assert detect() == (None, "none found")
    (tree / "package.json").write_text('{"scripts": {"test": "jest"}}')
    assert detect() == ("npm test", "package.json")
    (tree / "Cargo.toml").write_text("[package]\n")
    assert detect() == ("cargo test", "Cargo.toml")
    (tree / "setup.py").write_text("")
    (tree / "tests").mkdir()
    assert detect() == ("python3 -m pytest -q", "pytest")
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")
    assert detect()[0] == f"{repo / '.venv/bin/python'} -m pytest -q"
    assert detect(configured="make check") == ("make check", "config [night] test")
    (tree / "dreamference.toml").write_text('[night]\ntest = "tox"\n')
    assert detect(configured="make check") == ("tox", "dreamference.toml [night] test")
    assert detect(override="true", configured="make check") == ("true", "--test")


# -- the host ------------------------------------------------------------------------------------

SGLANG_METRICS = """\
# HELP sglang:num_running_reqs x
sglang:num_running_reqs{engine_type="unified",tp_rank="0"} 1.0
sglang:num_queue_reqs{engine_type="unified",tp_rank="0"} 2.0
sglang:prompt_tokens_total{engine_type="unified"} 3.5e+07
sglang:max_total_num_tokens{engine_type="unified",tp_rank="0"} 144870.0
"""
VLLM_METRICS = """\
vllm:num_requests_running{model_name="m"} 0.0
vllm:num_requests_waiting{model_name="m"} 0.0
vllm:prompt_tokens_total{model_name="m"} 1234.0
vllm:cache_config_info{block_size="16",num_gpu_blocks="6000",model_name="m"} 1.0
"""


def test_metrics_are_read_under_both_engines_names():
    sglang = NightShiftHost.parse_metrics(SGLANG_METRICS)
    assert sglang == {"running": 3.0, "served": 3.5e7, "kv_pool": 144870.0}
    vllm = NightShiftHost.parse_metrics(VLLM_METRICS)
    assert vllm == {"running": 0.0, "served": 1234.0, "kv_pool": 96000.0}


def test_parallelism_follows_the_kv_pool_never_zero():
    # The default model: 144,870 pool tokens against a 262,144-token context. Dividing by the
    # context, as the spec first did, gives 0; per-task budgeting gives 2.
    assert NightShiftHost.parallelism(3, 144870.0, 49152) == 2
    assert NightShiftHost.parallelism(3, 1000.0, 49152) == 1
    assert NightShiftHost.parallelism(3, 0.0, 49152) == 3
    assert NightShiftHost.parallelism(2, 10_000_000.0, 49152) == 2


def test_tui_command_lines_are_told_from_the_rest():
    assert NightShiftHost.is_interactive([])
    assert NightShiftHost.is_interactive(["-m", "x", "fix the bug"])
    assert NightShiftHost.is_interactive(["resume", "--last"])
    assert not NightShiftHost.is_interactive(["exec", "--json", "task"])
    assert not NightShiftHost.is_interactive(["app-server"])
    assert not NightShiftHost.is_interactive(["night", "list"])


def test_sizes_parse():
    assert NightShiftHost.parse_size("8G") == 8 * 1024 ** 3
    assert NightShiftHost.parse_size("512M") == 512 * 1024 ** 2
    assert NightShiftHost.parse_size("1024") == 1024


# -- admission and scheduling --------------------------------------------------------------------

class FakeHost(NightShiftHost):
    model = ("m", 262144)
    safe = True
    available = 40 * 1024 ** 3
    heavy = []
    sessions = []
    samples = []

    @classmethod
    def served_model(cls, host, timeout=3.0):
        return cls.model

    @classmethod
    def host_safety_ok(cls):
        return cls.safe

    @classmethod
    def mem_available_bytes(cls):
        return cls.available

    @classmethod
    def heavy_jobs(cls):
        return list(cls.heavy)

    @classmethod
    def interactive_puffin_pids(cls, puffin_bin):
        return list(cls.sessions)

    @classmethod
    def metrics(cls, host, timeout=3.0):
        if len(cls.samples) > 1:
            return cls.samples.pop(0)
        return cls.samples[0] if cls.samples else {"running": 0.0, "served": 1.0, "kv_pool": 144870.0}

    @classmethod
    def scope_memory_current(cls, unit):
        return 0


@pytest.fixture
def fake_host(monkeypatch):
    for name, value in (("model", ("m", 262144)), ("safe", True), ("available", 40 * 1024 ** 3),
                        ("heavy", []), ("sessions", []), ("samples", [])):
        monkeypatch.setattr(FakeHost, name, value)
    monkeypatch.setattr(NightShiftRunner, "host", FakeHost)
    monkeypatch.setattr(NightShiftRunner, "sleep", staticmethod(lambda seconds: None))
    return FakeHost


def far_end():
    return datetime.now().astimezone() + timedelta(hours=1)


@pytest.mark.parametrize("change, reason", [
    (("model", None), "not answering"),
    (("safe", False), "host-safety"),
    (("available", 4 * 1024 ** 3), "GiB of memory is available"),
    (("heavy", ["a puffin build holds the build lock"]), "build lock"),
    (("sessions", [4242]), "puffin session is open"),
])
def test_each_admission_check_stops_the_night_on_its_own(fake_host, monkeypatch, change, reason):
    monkeypatch.setattr(FakeHost, change[0], change[1])
    assert reason in NightShiftRunner.admit("http://x", "puffin", 0, far_end())


def test_admission_waits_for_the_model_to_be_idle(fake_host, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("dreamference.night_shift.night_shift_runner.time.time", lambda: clock["now"])
    def advance(seconds):
        clock["now"] += seconds
    monkeypatch.setattr(NightShiftRunner, "sleep", staticmethod(advance))
    busy = {"running": 1.0, "served": 10.0, "kv_pool": 1.0}
    moved = {"running": 0.0, "served": 20.0, "kv_pool": 1.0}
    quiet = {"running": 0.0, "served": 20.0, "kv_pool": 1.0}
    FakeHost.samples = [busy, moved, quiet]
    assert NightShiftRunner.wait_for_idle("http://x", "puffin", 1, far_end()) is None
    # Idle for a full minute counted from the last change, not from the start.
    assert clock["now"] >= 1000.0 + 60 + 30


def test_a_window_that_closes_while_waiting_reports_it(fake_host):
    FakeHost.samples = [{"running": 1.0, "served": 1.0, "kv_pool": 1.0}]
    past = datetime.now().astimezone() - timedelta(minutes=1)
    assert "until the window closed" in NightShiftRunner.wait_for_idle("http://x", "puffin", 10, past)


def test_round_robin_alternates_repositories():
    tasks = [{"id": i, "repo": repo} for i, repo in enumerate("AAAB")]
    assert [task["id"] for task in NightShiftRunner.round_robin(tasks)] == [0, 3, 1, 2]


def test_window_end_is_the_next_such_time():
    now = datetime(2026, 10, 1, 1, 30).astimezone()
    assert NightShiftRunner.window_end(now, None, None, "01:00-07:00").hour == 7
    assert NightShiftRunner.window_end(now, None, None, "01:00-07:00").day == 1
    assert NightShiftRunner.window_end(now, "01:00", None, "x").day == 2
    assert NightShiftRunner.window_end(now, None, 30, "x") == now + timedelta(minutes=30)


def test_an_outside_request_or_session_blocks_the_next_start(fake_host):
    settings = NightShiftSettings({})
    FakeHost.samples = [{"running": 1.0, "served": 1.0, "kv_pool": 1.0}]
    assert "not the night run's" in NightShiftRunner.start_blocker("http://x", "p", [], settings)
    FakeHost.samples = [{"running": 0.0, "served": 1.0, "kv_pool": 1.0}]
    FakeHost.sessions = [1]
    assert "session is open" in NightShiftRunner.start_blocker("http://x", "p", [], settings)
    FakeHost.sessions = []
    assert NightShiftRunner.start_blocker("http://x", "p", [], settings) is None
    FakeHost.available = 12 * 1024 ** 3
    assert "GiB is free" in NightShiftRunner.start_blocker("http://x", "p", [], settings)


def test_one_wait_is_noted_once_whatever_its_figures(fake_host, monkeypatch):
    # Live run, 2026-10-02: the memory reason's free-memory figure changed on every poll, and the
    # report carried "waiting to start the next task" eight times in one minute.
    reasons = iter(["11.6 GiB is free after the running tasks' allowance; one more needs 16",
                    "11.8 GiB is free after the running tasks' allowance; one more needs 16",
                    "a puffin session is open",
                    "11.7 GiB is free after the running tasks' allowance; one more needs 16"])
    clock = {"now": 1000.0}
    monkeypatch.setattr("dreamference.night_shift.night_shift_runner.time.time", lambda: clock["now"])
    monkeypatch.setattr(NightShiftRunner, "sleep", staticmethod(lambda seconds: clock.update(now=clock["now"] + seconds)))
    monkeypatch.setattr(NightShiftRunner, "start_blocker", classmethod(
        lambda cls, *args: next(reasons, "11.9 GiB is free after the running tasks' allowance; one more needs 16")))
    notes = []
    end = datetime.fromtimestamp(1000.0 + 4 * 5 + 1).astimezone()
    NightShiftRunner.schedule(Path("/nonexistent"), [{"id": "t", "repo": "r"}], NightShiftSettings({}),
                              "puffin", "http://x", end, 1, notes)
    waits = [note for note in notes if "waiting to start" in note]
    assert len(waits) == 3, waits
    assert "11.6 GiB" in waits[0] and "session is open" in waits[1] and "11.7 GiB" in waits[2]


def test_a_whole_night_runs_three_tasks_and_writes_the_report(setup, fake_host, monkeypatch):
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "change")
    for index in range(3):
        queue(setup["night"], setup["repo"], task_text=f"Task {index}", test="test -f hello.txt",
              task_id=f"20261001-0100-a{index}0")
    code = NightShiftRunner.run(minutes=5, idle_minutes=0, night_dir=setup["night"],
                                puffin_bin=setup["puffin"], vllm_host="http://x",
                                settings=NightShiftSettings({"max_parallel": 2}))
    assert code == 0
    statuses = [task["status"] for task in NightShiftQueue.tasks(setup["night"])]
    assert statuses == ["done", "done", "done"]
    branches = git(setup["repo"], "branch", "--list", "night/*", "--format=%(refname:short)").stdout.split()
    assert len(branches) == 3
    report = next((setup["night"] / "reports").glob("*.md")).read_text()
    assert f"## {setup['repo']}" in report and report.count("— done") == 3
    # 90% of the 144,870-token pool holds one 65,536-token task budget, not two
    # (test_night_shift_compaction.py has the budget itself).
    assert "Up to 1 task(s) at once" in report
    assert git(setup["repo"], "status", "--porcelain").stdout == ""


class FakeIndex(NightShiftIndex):
    """Records what would have been indexed; nothing in the suite runs `puffin-code`."""
    binary = "/opt/puffin-code"
    outcome = "3 ok"
    calls = []

    @classmethod
    def executable(cls):
        return cls.binary

    @classmethod
    def refresh(cls, binary, repo, timeout_s):
        # The first task has not started: its branch does not exist yet.
        branches = git(repo, "branch", "--list", "night/*").stdout.split()
        cls.calls.append((binary, str(repo), timeout_s, len(branches)))
        return cls.outcome


@pytest.fixture
def fake_index(monkeypatch):
    monkeypatch.setattr(FakeIndex, "calls", [])
    monkeypatch.setattr(FakeIndex, "binary", "/opt/puffin-code")
    monkeypatch.setattr(NightShiftRunner, "index", FakeIndex)
    return FakeIndex


def test_each_repository_is_indexed_once_before_its_tasks_start(setup, fake_host, fake_index, monkeypatch):
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "change")
    for index in range(2):
        queue(setup["night"], setup["repo"], task_text=f"Task {index}", task_id=f"20261001-0100-b{index}0")
    assert NightShiftRunner.run(minutes=30, idle_minutes=0, night_dir=setup["night"], puffin_bin=setup["puffin"],
                                vllm_host="http://x", settings=NightShiftSettings({"index_timeout": "5m"})) == 0
    assert [(call[0], call[1], call[3]) for call in fake_index.calls] == [("/opt/puffin-code", str(setup["repo"]), 0)]
    assert fake_index.calls[0][2] == 300
    report = next((setup["night"] / "reports").glob("*.md")).read_text()
    assert f"Code index of {setup['repo']}: 3 ok." in report
    assert [task["status"] for task in NightShiftQueue.tasks(setup["night"])] == ["done", "done"]


def test_the_index_refresh_can_be_switched_off_and_needs_puffin_code(setup, fake_host, fake_index, monkeypatch):
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "change")
    queue(setup["night"], setup["repo"])
    NightShiftRunner.run(minutes=30, idle_minutes=0, night_dir=setup["night"], puffin_bin=setup["puffin"],
                         vllm_host="http://x", settings=NightShiftSettings({"index": False}))
    assert fake_index.calls == []
    # Not installed: nothing runs, and nothing is said.
    queue(setup["night"], setup["repo"], task_id="20261001-0100-c00")
    fake_index.binary = None
    NightShiftRunner.run(minutes=30, idle_minutes=0, night_dir=setup["night"], puffin_bin=setup["puffin"],
                         vllm_host="http://x", settings=NightShiftSettings({}))
    assert fake_index.calls == []
    # A refused admission indexes nothing either: the run never started.
    queue(setup["night"], setup["repo"], task_id="20261001-0100-d00")
    fake_index.binary = "/opt/puffin-code"
    FakeHost.model = None
    NightShiftRunner.run(minutes=30, idle_minutes=0, night_dir=setup["night"], puffin_bin=setup["puffin"],
                         vllm_host="http://x", settings=NightShiftSettings({}))
    assert fake_index.calls == []


def test_the_index_refresh_takes_at_most_half_of_what_is_left(fake_host, fake_index, tmp_path):
    notes = []
    tasks = [{"repo": str(tmp_path)}, {"repo": str(tmp_path)}, {"repo": str(tmp_path / "gone")}]
    end = datetime.now().astimezone() + timedelta(minutes=10)
    NightShiftRunner.refresh_indexes(tasks, NightShiftSettings({}), end, notes)
    assert len(fake_index.calls) == 1 and 290 <= fake_index.calls[0][2] <= 300
    # With under two minutes left, the tasks get them all.
    notes.clear()
    NightShiftRunner.refresh_indexes(tasks[:1], NightShiftSettings({}), datetime.now().astimezone() + timedelta(seconds=90), notes)
    assert len(fake_index.calls) == 1 and "the window is nearly over" in notes[0]


def test_index_output_is_condensed_for_the_report():
    output = (
        "skipped: scip-go for svc: runs on demand (`puffin-code index`)\n"
        "puffin-index-0a1b2c3d4e-codebase-memory: ok\n"
        "puffin-index-0a1b2c3d4e-scip-python-dreamference: ok\n"
        "puffin-index-0a1b2c3d4e-rust-analyzer-puffin-code-rs: deferred: memory\n"
    )
    assert NightShiftIndex.summarise(output) == "2 ok, 1 deferred (rust-analyzer-puffin-code-rs deferred: memory)"
    assert NightShiftIndex.summarise("skipped: codebase-memory-mcp is not installed\n") == "nothing to index"


def test_the_installed_puffin_code_is_the_only_one_used(tmp_path, monkeypatch):
    monkeypatch.setattr("dreamference.night_shift.night_shift_index.INSTALL_DIR", str(tmp_path))
    assert NightShiftIndex.executable() is None
    (tmp_path / "bin").mkdir()
    binary = tmp_path / "bin" / "puffin-code"
    binary.write_text("#!/bin/sh\necho \"puffin-index-x-codebase-memory: ok\"\n")
    binary.chmod(0o755)
    assert NightShiftIndex.executable() == str(binary)
    # A real run of the stand-in: foreground, in the repository, its output condensed.
    assert NightShiftIndex.refresh(str(binary), tmp_path, 30) == "1 ok"
    binary.write_text("#!/bin/sh\necho boom\nexit 4\n")
    assert NightShiftIndex.refresh(str(binary), tmp_path, 30) == "not refreshed (exit 4): boom"


def test_a_refused_admission_keeps_the_queue_and_says_why(setup, fake_host):
    queue(setup["night"], setup["repo"])
    FakeHost.model = None
    assert NightShiftRunner.run(minutes=5, idle_minutes=0, night_dir=setup["night"],
                                puffin_bin=setup["puffin"], vllm_host="http://x",
                                settings=NightShiftSettings({})) == 0
    assert NightShiftQueue.tasks(setup["night"])[0]["status"] == "queued"
    report = next((setup["night"] / "reports").glob("*.md")).read_text()
    assert "Not run: the model server at http://x is not answering" in report


def test_a_second_runner_is_refused_while_the_lock_is_held(setup, fake_host):
    queue(setup["night"], setup["repo"])
    with NightShiftQueue.runner_lock(setup["night"]) as held:
        assert held
        assert NightShiftQueue.runner_active(setup["night"])
        assert NightShiftRunner.run(minutes=5, night_dir=setup["night"], puffin_bin=setup["puffin"],
                                    vllm_host="http://x", settings=NightShiftSettings({})) == 1
    assert not NightShiftQueue.runner_active(setup["night"])


def test_build_index_and_server_start_refuse_during_a_night_run(tmp_path, monkeypatch):
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    with NightShiftQueue.runner_lock(tmp_path / "night") as held:
        assert held
        with pytest.raises(SystemExit):
            DreamferenceCLIController._refuse_during_night_run("codex build")
    DreamferenceCLIController._refuse_during_night_run("codex build")  # free again: no exit


# -- the queue format, shared with the launcher --------------------------------------------------

def test_a_record_written_by_the_launcher_reads_and_round_trips(setup):
    record = queue(setup["night"], setup["repo"])
    NightShiftQueue.transition(setup["night"], record["id"], "running", attempts=1)
    stored = json.loads((setup["night"] / "tasks" / f"{record['id']}.json").read_text())
    assert stored["status"] == "running" and stored["attempts"] == 1
    assert [entry["status"] for entry in stored["history"]] == ["queued", "running"]
    datetime.fromisoformat(stored["history"][-1]["at"])  # RFC 3339 with offset, as the launcher writes
    assert not list((setup["night"] / "tasks").glob(".*.tmp"))


# -- the timer -----------------------------------------------------------------------------------

def test_enable_writes_absolute_paths_and_the_window(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(NightShiftScheduler, "systemctl",
                        staticmethod(lambda args: commands.append(args) or subprocess.CompletedProcess(args, 0, "", "")))
    monkeypatch.setattr(NightShiftScheduler, "lingering", classmethod(lambda cls: True))
    assert NightShiftScheduler.enable("01:30-06:15")
    timer = (NightShiftScheduler.unit_dir() / "puffin-night.timer").read_text()
    service = (NightShiftScheduler.unit_dir() / "puffin-night.service").read_text()
    assert timer.startswith("# Night Shift window: 01:30-06:15\n")
    assert "OnCalendar=*-*-* 01:30:00" in timer
    exec_start = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    assert exec_start.split("=", 1)[1].startswith("/") and exec_start.endswith("night run --until 06:15")
    assert "/.local/bin" in service and "/.cargo/bin" in service
    assert commands == [["daemon-reload"], ["enable", "--now", "puffin-night.timer"]]
    assert "window 01:30-06:15" in NightShiftScheduler.status()
    assert NightShiftScheduler.disable()
    assert not (NightShiftScheduler.unit_dir() / "puffin-night.timer").exists()


def test_settings_parse_durations_and_windows():
    settings = NightShiftSettings({"task_timeout": "2h", "test_timeout": 15, "window": "23:00-05:00"})
    assert settings.task_timeout_s == 7200 and settings.test_timeout_s == 900
    start, end = NightShiftSettings.parse_window(settings.window)
    assert (start.hour, end.hour) == (23, 5)
    with pytest.raises(ValueError):
        NightShiftSettings.parse_duration("soon")


def test_report_rows_carry_what_the_spec_lists():
    started = datetime(2026, 10, 1, 1, 0)
    tasks = [
        {"id": "a", "repo": "/r", "base": "b" * 40, "task": "Fix it\nmore", "status": "done",
         "result": {"branch": "night/a", "diff_stat": " x | 1 +\n 1 file changed, 1 insertion(+)",
                    "test_command": "pytest", "test_source": "pytest", "test_result": "passed",
                    "attempts": 1, "nudges": 0, "wall_s": 75}},
        {"id": "b", "repo": "/r", "base": "b" * 40, "task": "Other", "status": "stalled",
         "result": {"last_message": "I'll now do it.", "attempts": 1, "nudges": 2, "wall_s": 5}},
    ]
    text = NightShiftReport.render(started, tasks, ["note one"])
    assert "## /r" in text and "### a — done" in text and "1 file changed, 1 insertion(+)" in text
    assert "`pytest` (pytest): passed" in text and "1 min 15 s" in text
    assert "Last message: I'll now do it." in text
    assert f"git -C /r diff {'b' * 12}..night/a" in text
    assert "## Notes" in text
