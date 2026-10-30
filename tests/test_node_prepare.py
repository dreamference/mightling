"""`NodePrepare`: applying the steps as root, and what each reading decides.

Nothing runs as root and nothing changes the machine: every command, group, account, profile and
systemd reading is a stand-in.
"""

import os
from types import SimpleNamespace

import pytest

from dreamference.node import node_prepare
from dreamference.node.node_prepare import TELEMETRY_UNIT, NodePrepare
from dreamference.node.node_service_file import NodeServiceFile
from dreamference.vllm_server import sandbox_prerequisite
from dreamference.vllm_server.sandbox_prerequisite import SandboxPrerequisite


@pytest.fixture
def as_root(monkeypatch):
    """Root acting for `owner`, every command recorded and answered from `fails`."""
    state = {"ran": [], "fails": set()}
    monkeypatch.setattr(node_prepare.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "owner")

    def execute(cls, command):
        state["ran"].append(command)
        return tuple(command) not in state["fails"]

    monkeypatch.setattr(NodePrepare, "_execute", classmethod(execute))
    return state


def readings(monkeypatch, *answers):
    """`steps` answering in turn: what is missing before, then after."""
    queue = list(answers)
    monkeypatch.setattr(NodePrepare, "steps", classmethod(lambda cls, user: queue.pop(0)))


MANUAL = {"name": "advertising", "why": "no Avahi", "manual": "install avahi-daemon"}
TWO = {"name": "two", "why": "w", "commands": [["first"], ["second"]]}
ONE = {"name": "one", "why": "w", "commands": [["only"]]}


def test_a_failed_command_skips_the_rest_of_its_step_but_not_the_next_step(
    monkeypatch, as_root, capsys
):
    as_root["fails"].add(("first",))
    readings(monkeypatch, [MANUAL, TWO, ONE], [])

    assert NodePrepare.run() is True

    assert as_root["ran"] == [["first"], ["only"]], "a manual step runs nothing"
    out = capsys.readouterr().out
    assert "install avahi-daemon" in out
    assert 'the rest of "two" was skipped' in out
    assert "ready for owner" in out


def test_a_step_still_missing_afterwards_fails_and_is_named(monkeypatch, as_root, capsys):
    readings(monkeypatch, [MANUAL, ONE], [MANUAL])
    assert NodePrepare.run() is False
    assert "Still to do: advertising." in capsys.readouterr().out


# -- docker


def group(members):
    return lambda name: SimpleNamespace(gr_mem=members)


def test_a_machine_without_a_docker_group_gets_a_manual_step_and_no_command(monkeypatch):
    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr(node_prepare.grp, "getgrnam", missing)
    step = NodePrepare._docker_step("owner")
    assert "manual" in step and "commands" not in step


def test_a_user_in_the_docker_group_by_membership_or_primary_group_needs_nothing(monkeypatch):
    monkeypatch.setattr(node_prepare.grp, "getgrnam", group(["owner"]))
    assert NodePrepare._docker_step("owner") is None
    monkeypatch.setattr(node_prepare.grp, "getgrnam", group([]))
    monkeypatch.setattr(NodePrepare, "_primary_group", classmethod(lambda cls, user: "docker"))
    assert NodePrepare._docker_step("owner") is None
    monkeypatch.setattr(NodePrepare, "_primary_group", classmethod(lambda cls, user: "owner"))
    assert NodePrepare._docker_step("owner")["commands"] == [["usermod", "-aG", "docker", "owner"]]


def test_an_unknown_account_has_no_primary_group(monkeypatch):
    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr(node_prepare.pwd, "getpwnam", missing)
    assert NodePrepare._primary_group("nobody-here") is None


# -- the Avahi service file


@pytest.fixture
def avahi(monkeypatch, tmp_path):
    services = tmp_path / "services"
    monkeypatch.setattr(node_prepare, "AVAHI_SERVICES_DIR", str(services))
    monkeypatch.setattr(NodeServiceFile, "service_path", services / "mightling-node.service")
    monkeypatch.setattr(NodePrepare, "_primary_group", classmethod(lambda cls, user: None))
    return services


def account(uid):
    return lambda name: SimpleNamespace(pw_uid=uid, pw_gid=uid)


def test_a_machine_without_avahi_gets_a_manual_step(avahi):
    step = NodePrepare._avahi_step("owner")
    assert "manual" in step and "commands" not in step


def test_a_service_file_the_user_owns_needs_nothing(monkeypatch, avahi):
    avahi.mkdir()
    NodeServiceFile.service_path.write_text("")
    monkeypatch.setattr(node_prepare.pwd, "getpwnam", account(os.getuid()))
    assert NodePrepare._avahi_step("owner") is None


@pytest.mark.parametrize("present", [True, False])
def test_a_service_file_missing_or_someone_elses_is_made_the_users(monkeypatch, avahi, present):
    avahi.mkdir()
    if present:
        NodeServiceFile.service_path.write_text("")
    monkeypatch.setattr(node_prepare.pwd, "getpwnam", account(os.getuid() + 1))
    step = NodePrepare._avahi_step("owner")
    assert step["commands"] == [
        [
            "install",
            "-m",
            "644",
            "-o",
            "owner",
            "-g",
            "owner",
            "/dev/null",
            str(NodeServiceFile.service_path),
        ]
    ]


# -- the sandbox profile and telemetry


@pytest.fixture
def profile(monkeypatch, tmp_path):
    path = tmp_path / "puffin-bwrap"
    monkeypatch.setattr(sandbox_prerequisite, "PROFILE_PATH", str(path))
    return path


def test_a_loaded_profile_needs_nothing_and_no_fix_means_no_step(monkeypatch, profile):
    def never(cls):
        pytest.fail("asked for a fix")

    profile.write_text("profile")
    monkeypatch.setattr(NodePrepare, "_profile_loaded", classmethod(lambda cls: True))
    monkeypatch.setattr(SandboxPrerequisite, "fix_commands", classmethod(never))
    assert NodePrepare._sandbox_step() is None

    monkeypatch.setattr(NodePrepare, "_profile_loaded", classmethod(lambda cls: False))
    monkeypatch.setattr(SandboxPrerequisite, "fix_commands", classmethod(lambda cls: None))
    assert NodePrepare._sandbox_step() is None

    monkeypatch.setattr(SandboxPrerequisite, "fix_commands", classmethod(lambda cls: [["fix"]]))
    assert NodePrepare._sandbox_step()["commands"] == [["fix"]]


class Profiles:
    def __init__(self, text):
        self.text = text

    def __call__(self, *_):
        return self

    def read_text(self):
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


@pytest.mark.parametrize(
    ("text", "loaded"),
    [
        ("other (enforce)\npuffin-bwrap (unconfined)\n", True),
        ("puffin-bwrap-old (enforce)\n", False),
        (PermissionError("profiles"), False),
    ],
)
def test_the_profile_is_loaded_only_when_the_kernel_lists_it_by_name(monkeypatch, text, loaded):
    monkeypatch.setattr(node_prepare, "Path", Profiles(text))
    assert NodePrepare._profile_loaded() is loaded


@pytest.mark.parametrize(
    ("enabled", "active", "needed"),
    [
        ("disabled", "inactive", False),
        ("", "", False),
        ("static", "inactive", True),
        ("disabled", "active", True),
    ],
)
def test_telemetry_is_turned_off_when_enabled_or_running(monkeypatch, enabled, active, needed):
    answers = {"is-enabled": enabled, "is-active": active}
    monkeypatch.setattr(
        NodePrepare, "_systemctl", classmethod(lambda cls, verb, unit: answers[verb])
    )
    step = NodePrepare._telemetry_step()
    assert (step is not None) is needed
    if needed:
        assert step["commands"] == [["systemctl", "disable", "--now", TELEMETRY_UNIT]]


# -- the one place commands run


def test_systemctl_that_cannot_run_reads_as_no_answer(monkeypatch):
    def broken(*_, **__):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(node_prepare.subprocess, "run", broken)
    assert NodePrepare._systemctl("is-active", TELEMETRY_UNIT) == ""
    monkeypatch.setattr(
        node_prepare.subprocess, "run", lambda *_, **__: SimpleNamespace(stdout="active\n")
    )
    assert NodePrepare._systemctl("is-active", TELEMETRY_UNIT) == "active"


def test_a_command_that_cannot_run_or_fails_is_a_failure(monkeypatch, capsys):
    def broken(*_, **__):
        raise FileNotFoundError("loginctl")

    monkeypatch.setattr(node_prepare.subprocess, "run", broken)
    assert NodePrepare._execute(["loginctl", "enable-linger", "owner"]) is False
    assert "loginctl" in capsys.readouterr().out

    codes = iter([1, 0])
    monkeypatch.setattr(
        node_prepare.subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=next(codes))
    )
    assert NodePrepare._execute(["loginctl", "enable-linger", "owner"]) is False
    assert NodePrepare._execute(["loginctl", "enable-linger", "owner"]) is True
