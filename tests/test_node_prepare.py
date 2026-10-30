"""`NodePrepare`: applying the steps as root, and what each reading decides.

Nothing runs as root and nothing changes the machine: every command, group, account, profile and
systemd reading is a stand-in.
"""

import os
from types import SimpleNamespace

import pytest

from dreamference.node import node_prepare
from dreamference.node.node_prepare import NodePrepare
from dreamference.node.node_service_file import NodeServiceFile


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
