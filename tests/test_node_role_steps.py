"""`NodeRoleProvisioner`'s steps one at a time: what each does when the machine says no.

The run and the session are stand-ins: no ssh, sudo, docker or `ssh-keygen` command runs, and every
command a step sends is recorded and answered by the first marker it contains.
"""

import subprocess

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
