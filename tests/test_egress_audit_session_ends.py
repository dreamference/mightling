"""How a traced `ling` session ends when it never answers or never starts (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §3).

No test here runs strace or ling: a stand-in on PATH plays strace, and the model server is never asked.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from dreamference.audit import CompatPrintSession, EgressAudit, TuiSession

HOST = "http://localhost:8000"
# The class itself: tests/conftest.py wraps subprocess.Popen in a guard while a test runs.
POPEN = subprocess.Popen


def _stand_in(tmp_path, monkeypatch, body: str) -> None:
    strace = tmp_path / "bin" / "strace"
    strace.parent.mkdir(exist_ok=True)
    strace.write_text(body)
    strace.chmod(0o755)
    monkeypatch.setenv("PATH", f"{strace.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(EgressAudit, "declared_exceptions", classmethod(lambda cls: []))


def _gone(pid: int) -> bool:
    """True once `pid` has exited (a zombie waiting for its new parent counts as gone)."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return True
        if state == "Z":
            return True
        time.sleep(0.05)
    return False


def test_an_exec_session_that_never_answers_is_stopped_with_what_it_started(tmp_path, monkeypatch):
    child_pid = tmp_path / "child.pid"
    _stand_in(
        tmp_path,
        monkeypatch,
        f"""#!/bin/sh
sleep 300 &
echo $! > {child_pid}
sleep 300
""",
    )
    monkeypatch.setattr("dreamference.audit.egress_audit.SESSION_TIMEOUT_S", 1)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    started = time.monotonic()
    trace, replied, _ = EgressAudit.trace_session("/opt/ling", HOST, "hi", str(work_dir), "exec")
    assert time.monotonic() - started < 30
    assert replied is False and not trace.processes
    # The whole process group is stopped, not strace alone: nothing it started waits on.
    assert _gone(int(child_pid.read_text()))


def test_an_exec_session_that_never_answers_fails_the_audit(tmp_path, monkeypatch, capsys):
    _stand_in(tmp_path, monkeypatch, "#!/bin/sh\nexec sleep 300\n")
    monkeypatch.setattr("dreamference.audit.egress_audit.SESSION_TIMEOUT_S", 1)
    assert EgressAudit.run(mightling_bin="/opt/ling", vllm_host=HOST) == 2
    assert "trace failed" in capsys.readouterr().out


@pytest.mark.parametrize("web", [False, True])
def test_a_session_that_cannot_start_is_a_failed_trace_not_a_pass(
    web, tmp_path, monkeypatch, capsys
):
    # strace is found but cannot be run: its interpreter does not exist, so starting it raises.
    # PATH holds only the stand-in and git, so no other strace further down PATH is run instead.
    git = shutil.which("git")
    _stand_in(tmp_path, monkeypatch, "#!/nonexistent/interpreter\n")
    (tmp_path / "bin" / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert EgressAudit.run(mightling_bin="/opt/ling", vllm_host=HOST, web=web) == 2
    assert "trace failed" in capsys.readouterr().out


def test_a_session_that_ignores_the_stop_is_killed(tmp_path, monkeypatch):
    leader_pid = tmp_path / "leader.pid"
    _stand_in(
        tmp_path,
        monkeypatch,
        f"""#!/bin/sh
trap '' TERM
echo $$ > {leader_pid}
sleep 300
""",
    )
    monkeypatch.setattr("dreamference.audit.egress_audit.SESSION_TIMEOUT_S", 1)
    # The audit waits ten seconds for a stopped session before it kills it: one is enough here.
    wait = POPEN.wait
    monkeypatch.setattr(POPEN, "wait", lambda self, timeout=None: wait(self, min(timeout or 1, 1)))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _, replied, _ = EgressAudit.trace_session("/opt/ling", HOST, "hi", str(work_dir), "exec")
    assert replied is False
    assert _gone(int(leader_pid.read_text()))


@pytest.fixture
def after_build(monkeypatch):
    """`EgressAudit.after_build` with the model server, the tools on PATH and the sessions replaced."""
    state = {
        "which": lambda name: f"/usr/bin/{name}",
        "missing": [],
        "asked": [],
        "runs": [],
        "code": 0,
    }
    monkeypatch.setattr("shutil.which", lambda name: state["which"](name))
    monkeypatch.setattr(TuiSession, "missing_modules", classmethod(lambda cls: state["missing"]))
    monkeypatch.setattr(CompatPrintSession, "after_build", classmethod(lambda cls, run: []))

    def served(cls, host, timeout=3.0):
        state["asked"].append(host)
        return ("model", 4096)

    def run(cls, write_json=False, vllm_host=None, tui=False, **options):
        state["runs"].append({"tui": tui, "vllm_host": vllm_host})
        return state["code"]

    monkeypatch.setattr("dreamference.night_shift.NightShiftHost.served_model", classmethod(served))
    monkeypatch.setattr(EgressAudit, "run", classmethod(run))
    return state


def test_after_a_build_without_strace_the_audit_is_skipped_and_the_build_kept(after_build, capsys):
    after_build["which"] = lambda name: None if name == "strace" else f"/usr/bin/{name}"
    assert EgressAudit.after_build(vllm_host=HOST) is None
    out = capsys.readouterr().out
    assert (
        "Egress audit skipped: strace is not installed" in out and "ling-admin audit egress" in out
    )
    # Neither the model server nor a session is started for an audit that cannot trace.
    assert after_build["asked"] == [] and after_build["runs"] == []


def test_after_a_build_without_the_terminal_modules_only_exec_is_traced_and_its_verdict_kept(
    after_build, capsys
):
    after_build["missing"] = ["pexpect", "pyte"]
    after_build["code"] = 1
    assert EgressAudit.after_build(vllm_host=HOST) == 1
    out = capsys.readouterr().out
    assert after_build["runs"] == [{"tui": False, "vllm_host": HOST}]
    assert (
        "full-screen interface was not traced" in out
        and "This build reaches something it should not" in out
    )


def test_after_a_build_the_configured_model_server_is_audited_by_default(after_build):
    from dreamference.config import DreamferenceConfig

    configured = DreamferenceConfig().vllm_host
    assert EgressAudit.after_build() == 0
    assert after_build["asked"] == [configured]
    assert [run["vllm_host"] for run in after_build["runs"]] == [configured, configured]
