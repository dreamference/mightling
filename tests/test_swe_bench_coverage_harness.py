"""The harness wrapper's refusals: no venv, a failed install, a failed grading, the memory cap."""

import subprocess

from dreamference.swe_bench import swe_bench_harness as module
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_harness import SweBenchHarness


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def test_no_virtualenv_means_no_installed_version():
    assert SweBenchHarness.installed_version() is None


def test_install_gives_up_when_the_virtualenv_cannot_be_created(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        SweBenchHarness,
        "execute",
        classmethod(lambda cls, cmd, **kw: seen.append(cmd) or done(1, err="no venv")),
    )
    assert SweBenchHarness.install() is False
    assert len(seen) == 1  # no pip call after the failed venv
    assert "Could not create" in capsys.readouterr().out


def test_install_reports_a_failed_pip_install(monkeypatch, capsys):
    monkeypatch.setattr(SweBenchHarness, "installed_version", classmethod(lambda cls: None))
    python = module.Path(SweBenchHarness.tool("python"))
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("")
    monkeypatch.setattr(
        SweBenchHarness, "execute", classmethod(lambda cls, cmd, **kw: done(1, err="boom"))
    )
    assert SweBenchHarness.install() is False
    assert "pip install swebench==" in capsys.readouterr().out


def test_a_nonzero_harness_exit_is_reported_and_returned_as_false(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        SweBenchHarness, "execute", classmethod(lambda cls, cmd, **kw: done(3, "out", "err"))
    )
    ok = SweBenchHarness.evaluate(
        tmp_path / "w", tmp_path / "d.jsonl", "r-1", ["a__a-1"], None, 1, 60
    )
    assert ok is False
    assert "swebench eval exited 3" in capsys.readouterr().out
    assert (tmp_path / "w" / "harness-r-1.log").read_text() == "outerr"


def test_missing_snapshot_gives_no_rows_and_no_metadata():
    assert SweBenchHarness.rows("verified") == []
    assert SweBenchHarness.dataset_meta("verified") == {}


class StopAfter:
    """An event that is set after a number of waits."""

    def __init__(self, waits):
        self.waits = waits

    def wait(self, seconds):
        self.waits -= 1
        return self.waits < 0


def test_only_containers_of_this_grading_get_a_memory_cap_and_only_once(monkeypatch):
    calls = []

    def fake_run(args, timeout=None, input_text=None):
        calls.append(args)
        if args[0] == "ps":
            return done(0, "sweb.eval.a.run1 sweb.eval.b.other sweb.eval.c.run1\n")
        return done()

    monkeypatch.setattr(SweBenchDocker, "run", classmethod(lambda cls, *a, **k: fake_run(*a, **k)))
    SweBenchHarness._cap_containers("run1", "8g", StopAfter(2))
    updates = [c for c in calls if c[0] == "update"]
    assert [c[-1] for c in updates] == ["sweb.eval.a.run1", "sweb.eval.c.run1"]
    assert all(c[:5] == ["update", "--memory", "8g", "--memory-swap", "8g"] for c in updates)
