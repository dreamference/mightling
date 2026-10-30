"""The compatibility audit's print session when it cannot start, does not end in time or will not stop.

The process and its signals are stand-ins: nothing here starts a session or signals a real process.
"""

import signal
import subprocess

import pytest

from dreamference.audit import CompatPrintSession, compat_print_session


class Session:
    """A started session that ends only on the signals listed in `ends_on`."""

    def __init__(self, ends_on=(), never_ends=False):
        self.pid = 4242
        self.ends_on = set(ends_on)
        self.never_ends = never_ends
        self.signals = []

    def wait(self, timeout=None):
        if self.never_ends or not self.signals or self.signals[-1] not in self.ends_on:
            raise subprocess.TimeoutExpired("ling", timeout)
        return -self.signals[-1]


@pytest.fixture
def quiet_setup(monkeypatch):
    monkeypatch.setattr(CompatPrintSession, "write_fixture", classmethod(lambda cls, repo: None))
    monkeypatch.setattr(
        CompatPrintSession,
        "approve_fixture_server",
        classmethod(lambda cls, command, repo, env: None),
    )


def signals_to(session, monkeypatch, missing=False):
    def killpg(pid, sig):
        assert pid == session.pid
        session.signals.append(sig)
        if missing:
            raise ProcessLookupError

    monkeypatch.setattr(compat_print_session.os, "killpg", killpg)


def test_a_session_that_cannot_be_started_has_not_replied(quiet_setup, monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(compat_print_session.subprocess, "Popen", refuse)
    assert CompatPrintSession.run(["ling"], tmp_path, {}, tmp_path / "out.jsonl", 1) is False


def test_a_session_that_does_not_end_in_time_is_stopped_and_without_an_answer_has_not_replied(
    quiet_setup, monkeypatch, tmp_path
):
    session = Session(ends_on={signal.SIGTERM})
    started = []

    def start(command, **kwargs):
        started.append(kwargs)
        return session

    monkeypatch.setattr(compat_print_session.subprocess, "Popen", start)
    signals_to(session, monkeypatch)
    assert CompatPrintSession.run(["ling"], tmp_path, {}, tmp_path / "out.jsonl", 1) is False
    assert started[0]["start_new_session"] is True
    assert session.signals == [signal.SIGTERM]


def test_a_session_that_ignores_the_terminate_signal_is_killed(monkeypatch):
    session = Session(ends_on={signal.SIGKILL})
    signals_to(session, monkeypatch)
    CompatPrintSession.stop(session)
    assert session.signals == [signal.SIGTERM, signal.SIGKILL]


def test_a_session_whose_group_is_already_gone_is_sent_both_signals_and_nothing_fails(monkeypatch):
    session = Session(never_ends=True)
    signals_to(session, monkeypatch, missing=True)
    CompatPrintSession.stop(session)
    assert session.signals == [signal.SIGTERM, signal.SIGKILL]
