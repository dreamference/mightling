"""`NodeRoleProvisioner`'s steps one at a time: what each does when the machine says no.

The run and the session are stand-ins: no ssh, sudo, docker or `ssh-keygen` command runs, and every
command a step sends is recorded and answered by the first marker it contains.
"""

import json
import subprocess

import pytest

from dreamference.node.node_provisioner import NodeProvisioner
from dreamference.node.node_role_provisioner import (
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
