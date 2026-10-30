"""`NodeRoleProvisioner`'s steps one at a time: what each does when the machine says no.

The run and the session are stand-ins: no ssh, sudo, docker or `ssh-keygen` command runs, and every
command a step sends is recorded and answered by the first marker it contains.
"""

import json
import subprocess

import pytest

from dreamference.node import node_role_provisioner
from dreamference.node.node_provisioner import NodeProvisioner
from dreamference.node.node_role_provisioner import (
    DERIVE_FLAG,
    NodeRoleProvisioner,
)

HOST = "new-node"
INSTALL = "install -o root -g root"


class Run:
    """The provisioning run: options, passwords, results, and the real failure bookkeeping."""

    _fail = NodeProvisioner._fail
    _done = NodeProvisioner._done

    def __init__(self, **options):
        self.options = options
        self.results = {HOST: {"host": HOST, "done": []}}
        self.passwords = {}


class Session:
    """A session answering each command by the first marker in `answers` it contains, else 0."""

    def __init__(self, answers=None, identity=None, user="user"):
        self.host, self.user, self.identity = HOST, user, identity
        self.answers = answers or {}
        self.commands = []
        self.key_logins = []

    def answer(self, line):
        self.commands.append(line)
        for marker, (code, out, err) in self.answers.items():
            if marker in line:
                return subprocess.CompletedProcess(line, code, out, err)
        return subprocess.CompletedProcess(line, 0, "", "")

    def run(self, script, input_text=None, **_):
        return self.answer(script)

    def sudo(self, line, password):
        return self.answer(line)

    def run_admin(self, argv, timeout=None, docker=False):
        return self.answer("ling-admin " + " ".join(argv))

    def key_login_works(self):
        return self.key_logins.pop(0)


def role(role="night", **options):
    run = Run(**options)
    return NodeRoleProvisioner(run, role), run


def sent(session, marker):
    return [line for line in session.commands if marker in line]


def test_a_user_name_sudoers_would_misread_installs_no_drop_in():
    steps, run = role()
    session = Session(user="user1 ALL=(ALL) NOPASSWD: ALL #")
    assert steps.sudoers(HOST, session) is False
    assert run.results[HOST]["failed"] == "sudoers"
    assert sent(session, INSTALL) == [] and sent(session, "cat /etc/sudoers.d") == []


# The docker daemon file


@pytest.mark.parametrize(
    "answer", [(1, "", "Permission denied"), (0, "{not json", ""), (0, '{"runtimes": []}', "")]
)
def test_a_daemon_json_that_cannot_be_read_or_parsed_is_left_alone(answer):
    steps, run = role()
    session = Session({"daemon.json; fi": answer})
    assert steps.docker_daemon(HOST, session) is False
    assert run.results[HOST]["failed"] == "docker daemon.json"
    assert run.results[HOST]["reason"].startswith("left alone")
    assert sent(session, INSTALL) == [] and sent(session, "restart docker") == []


def test_an_nvidia_runtime_already_there_is_kept_and_one_with_no_path_is_replaced():
    own = {"path": "/usr/local/bin/nvidia-container-runtime", "args": ["--debug"]}
    merged = json.loads(
        NodeRoleProvisioner.merge_daemon_json(json.dumps({"runtimes": {"nvidia": own}}))
    )
    assert merged["runtimes"]["nvidia"] == own and merged["max-download-attempts"] == 100

    empty = json.dumps({"runtimes": {"nvidia": {"path": ""}}, "max-download-attempts": 100})
    merged = json.loads(NodeRoleProvisioner.merge_daemon_json(empty))
    assert merged["runtimes"]["nvidia"]["path"] == "nvidia-container-runtime"


# The main node's key


@pytest.fixture
def keygen(monkeypatch):
    """`ssh-keygen` on this machine: records each argv and answers from `reply`."""
    state = {"argv": [], "reply": subprocess.CompletedProcess([], 0, "", "")}

    def run(argv, **_):
        state["argv"].append(argv)
        if isinstance(state["reply"], BaseException):
            raise state["reply"]
        return state["reply"]

    monkeypatch.setattr(node_role_provisioner.subprocess, "run", run)
    return state


def test_a_missing_public_half_is_derived_from_the_private_key(keygen, tmp_path):
    private = tmp_path / "id_ed25519"
    private.write_text("private")
    keygen["reply"] = subprocess.CompletedProcess([], 0, "ssh-ed25519 AAAAmainkey main\n", "")
    steps, _ = role()
    session = Session({"grep -qF": (0, "", "")}, identity=private)

    assert steps.authorize_key(HOST, session) is True

    assert keygen["argv"] == [["ssh-keygen", DERIVE_FLAG, "-f", str(private)]]
    assert (tmp_path / "id_ed25519.pub").read_text() == "ssh-ed25519 AAAAmainkey main\n"
    assert sent(session, "grep -qF 'ssh-ed25519 AAAAmainkey'")


def test_a_key_that_cannot_be_made_authorises_nothing(keygen, tmp_path):
    keygen["reply"] = subprocess.CompletedProcess([], 1, "", "Saving key failed")
    steps, run = role()
    session = Session(identity=tmp_path / "id_ed25519")
    assert steps.authorize_key(HOST, session) is False
    assert run.results[HOST]["reason"] == "Saving key failed"
    assert session.commands == [] and not (tmp_path / "id_ed25519.pub").exists()


def test_a_missing_ssh_keygen_is_a_failed_command_not_a_crash(keygen, tmp_path):
    keygen["reply"] = FileNotFoundError("ssh-keygen")
    private, public = tmp_path / "keys" / "id", tmp_path / "keys" / "id.pub"
    answer = NodeRoleProvisioner._make_key(["ssh-keygen", DERIVE_FLAG], private, public)
    assert answer.returncode == 1 and answer.stderr == "ssh-keygen"
    assert not public.exists()
