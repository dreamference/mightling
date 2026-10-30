"""What the sandbox prerequisite reads from the machine before it decides to ask.

A profile file it cannot read does not count as attaching to bubblewrap; a closed input is "not
now", never "fix it" with sudo; the restriction is known only from the kernel's own setting; and
only a person at a terminal on both ends is asked anything.
"""

import io
import sys

import pytest
from test_sandbox_prerequisite import machine  # noqa: F401 - fixture

from dreamference.vllm_server import SandboxPrerequisite, sandbox_prerequisite


def test_a_profile_that_cannot_be_read_is_skipped_and_the_rest_still_count(machine):  # noqa: F811
    (machine["profiles"] / "abstractions").mkdir()
    (machine["profiles"] / "local-bwrap").write_text("profile /usr/bin/bwrap {\n}\n")
    assert SandboxPrerequisite.profiles_attaching("/usr/bin/bwrap") == [
        str(machine["profiles"] / "local-bwrap")
    ]


def test_a_closed_input_is_not_now_and_nothing_runs_as_root(machine, monkeypatch):  # noqa: F811
    def closed(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    assert SandboxPrerequisite.gate("server", "start") is True
    assert machine["root"] == []
    assert not SandboxPrerequisite.decision_path().exists()


@pytest.mark.parametrize(("value", "restricted"), [("1\n", True), ("0\n", False)])
def test_the_restriction_is_read_from_the_kernels_setting(tmp_path, monkeypatch, value, restricted):
    sysctl = tmp_path / "apparmor_restrict_unprivileged_userns"
    sysctl.write_text(value)
    monkeypatch.setattr(sandbox_prerequisite, "USERNS_SYSCTL", str(sysctl))
    assert SandboxPrerequisite._userns_restricted() is restricted


def test_a_kernel_without_the_setting_leaves_the_restriction_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_prerequisite, "USERNS_SYSCTL", str(tmp_path / "missing"))
    assert SandboxPrerequisite._userns_restricted() is None


class _Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


@pytest.mark.parametrize(
    ("stdin", "stdout", "interactive"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
def test_only_a_terminal_on_both_ends_counts_as_a_person(monkeypatch, stdin, stdout, interactive):
    monkeypatch.setattr(sys, "stdin", _Stream(stdin))
    monkeypatch.setattr(sys, "stdout", _Stream(stdout))
    assert SandboxPrerequisite._interactive() is interactive
