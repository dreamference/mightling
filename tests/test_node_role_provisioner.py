"""`ling-admin node provision --role`: the role's steps, against stand-in machines.

Every machine is a `StandIn` behind a stand-in session (tests/test_fleet_provision.py); no ssh,
sudo or docker command runs.
"""

import base64
import json
import re
import shlex
import subprocess

import pytest

from dreamference.node import node_provisioner
from dreamference.node.fleet_session import FleetSession
from dreamference.node.node_pairing import NodePairing
from dreamference.node.node_provisioner import NodeProvisioner
from dreamference.node.node_role_provisioner import (
    DOCKER_DAEMON_JSON,
    SSHD_DROPIN,
    SSHD_DROPIN_PATH,
    SSHD_KEY_ONLY,
    SUDOERS_PATH,
    NodeRoleProvisioner,
)
from tests.test_fleet_provision import SECRET, FakeSession, StandIn, fleet  # noqa: F401

PUBLIC_KEY = "ssh-ed25519 AAAAmainkey <user>@<main>"
ADMIN = "/home/user/.local/share/dreamference/venv/bin/ling-admin"


class RoleSession(FakeSession):
    """A stand-in session that also keeps the files the role's steps read and write."""

    def __init__(self, host, user, run_dir, ssh_port=22, log_path=None):
        super().__init__(host, user, run_dir, ssh_port, log_path)
        self.identity = None
        files = self.machine.__dict__.setdefault("files", {})
        self.files = files

    def key_login_works(self):
        self.machine.commands.append("key login")
        return (
            self.machine.__dict__.get("key_login", [True]).pop(0)
            if self.machine.__dict__.get("key_login")
            else True
        )

    def run(self, script, input_text=None, terminal=False, secret_input=False, timeout=None):
        if "authorized_keys" in script and input_text:
            self.files["authorized_keys"] = self.files.get("authorized_keys", "") + input_text
        found = {
            "grep -qF": lambda: "AAAAmainkey" in self.files.get("authorized_keys", ""),
            "smoke-*.json": lambda: "smoke" in self.files,
            "test -d": lambda: "night" in self.files,
            "command -v cmake": lambda: "cmake" in self.files,
            "grep -qx mightling-forge": lambda: "forge" in self.files,
        }
        for marker, check in found.items():
            if marker in script:
                self.machine.commands.append(script)
                return subprocess.CompletedProcess(script, 0 if check() else 1, "", "")
        answers = {
            f"if [ -e {DOCKER_DAEMON_JSON} ]": lambda: self.files.get(DOCKER_DAEMON_JSON, ""),
            "readlink": lambda: f"{ADMIN}\n{ADMIN}\n",
            "df -Pk": lambda: "900",
            "nvcc": lambda: "Cuda compilation tools, release 13.0, V13.0.88",
        }
        for marker, answer in answers.items():
            if marker in script and "dgx-release" not in script:  # not the probe
                return subprocess.CompletedProcess(script, 0, answer(), "")
        made = {
            "mkdir -p $HOME/.local/share/dreamference/swe-bench": "night",
            "swe-bench smoke": "smoke",
            "forge on": "forge",
        }
        self.files.update({name: True for marker, name in made.items() if marker in script})
        return super().run(script, input_text, terminal, secret_input, timeout)

    def sudo(self, line, password):
        if line.startswith("cat "):
            self.machine.commands.append(f"sudo {line}")
            path = line.split()[1]
            return subprocess.CompletedProcess(
                line, 0 if path in self.files else 1, self.files.get(path, ""), ""
            )
        if line == f"sh -c {shlex.quote(SSHD_KEY_ONLY)}":
            self.machine.commands.append(f"sudo {line}")
            return subprocess.CompletedProcess(
                line, 0 if SSHD_DROPIN_PATH in self.files else 1, "", ""
            )
        if line.startswith("grep -lE"):
            self.machine.commands.append(f"sudo {line}")
            return subprocess.CompletedProcess(
                line, 0, self.machine.__dict__.get("leftover", ""), ""
            )
        written = re.search(r"echo (\S+) \| base64 -d.* \"\$f\" (\S+);", line)
        if written:
            self.files[written.group(2)] = base64.b64decode(written.group(1)).decode()
        if "cmake ninja-build" in line:
            self.files["cmake"] = True
        return super().sudo(line, password)


@pytest.fixture
def role_fleet(request, monkeypatch, tmp_path):
    harness = request.getfixturevalue("fleet")
    key = tmp_path / "id_ed25519"
    key.write_text("private\n")
    (tmp_path / "id_ed25519.pub").write_text(PUBLIC_KEY + "\n")
    monkeypatch.setattr(node_provisioner, "FleetSession", RoleSession)
    monkeypatch.setattr(NodePairing, "ssh_config", classmethod(lambda cls, host: (None, key)))
    monkeypatch.setattr(
        NodePairing, "paired", classmethod(lambda cls: list(harness["paired"].values()))
    )
    synced = []
    from dreamference.forge.forge_mirrors import ForgeMirrors

    monkeypatch.setattr(
        ForgeMirrors, "sync", classmethod(lambda cls, quiet=False: synced.append(quiet) or 0)
    )
    return {**harness, "synced": synced}


def provision(role="night", dry_run=False):
    options = {
        "user": "user",
        "role": role,
        "dry_run": dry_run,
        "model": "qwen3.8-27b-nvfp4-dflash2",
    }
    return NodeProvisioner(["new-node"], options).run()


def test_daemon_json_is_merged_never_overwritten():
    merged = json.loads(
        NodeRoleProvisioner.merge_daemon_json(
            json.dumps(
                {
                    "log-driver": "journald",
                    "runtimes": {"runc": {"path": "runc"}},
                    "max-download-attempts": 5,
                }
            )
        )
    )
    assert merged["log-driver"] == "journald" and merged["runtimes"]["runc"] == {"path": "runc"}
    assert merged["runtimes"]["nvidia"] == {"args": [], "path": "nvidia-container-runtime"}
    assert merged["max-download-attempts"] == 100
    assert NodeRoleProvisioner.merge_daemon_json(json.dumps(merged)) is None  # already there
    assert json.loads(NodeRoleProvisioner.merge_daemon_json(""))["max-download-attempts"] == 100
    with pytest.raises(ValueError):
        NodeRoleProvisioner.merge_daemon_json("[1]")
    with pytest.raises(ValueError):
        NodeRoleProvisioner.merge_daemon_json("{not json")


def test_the_sudoers_drop_in_is_limited_to_named_commands():
    text = NodeRoleProvisioner.render_sudoers("user", [ADMIN])
    assert f"{ADMIN} node prepare *" in text
    assert f"{ADMIN} host setup, \\\n" in text
    assert text.rstrip().endswith("user ALL=(root) NOPASSWD: MIGHTLING_AGENTS")
    assert not re.search(r"NOPASSWD:\s*ALL\b", text)
    assert "/usr/bin/systemctl, /usr/bin/apt-get, /usr/bin/apt, /usr/bin/docker" in text
    for user, paths in (
        ("user1 ALL", []),
        ("user1", ["/x/ling-admin, ALL"]),
        ("user1", ["rel/ling-admin"]),
    ):
        with pytest.raises(ValueError):
            NodeRoleProvisioner.render_sudoers(user, paths)


def test_a_role_run_provisions_a_fresh_machine_in_order(role_fleet, capsys):
    machine = FakeSession.machines["new-node"] = StandIn()
    assert provision() == 0
    assert role_fleet["asked"] == ["Password for user on new-node: "]  # once, by getpass
    commands = machine.commands
    files = machine.files
    assert PUBLIC_KEY in files["authorized_keys"]
    merged = json.loads(files[DOCKER_DAEMON_JSON])
    assert merged["max-download-attempts"] == 100 and "nvidia" in merged["runtimes"]
    assert "NOPASSWD: MIGHTLING_AGENTS" in files[SUDOERS_PATH] and ADMIN in files[SUDOERS_PATH]
    assert files[SSHD_DROPIN_PATH] == SSHD_DROPIN
    markers = (
        "authorized_keys",
        "install.sh",
        "node prepare",
        "restart docker",
        "visudo -cf",
        "node enable",
        "forge on",
        "key login",
        "sshd -t",
        "swe-bench setup",
        "swe-bench smoke",
        "audit egress",
        "sudo -k",
    )
    order = [next((i for i, c in enumerate(commands) if marker in c), -1) for marker in markers]
    assert -1 not in order and order == sorted(order), list(zip(markers, order, strict=True))
    # Docker restarts only when no model container runs; sudoers is checked before it is installed.
    sudoers = next(c for c in commands if "visudo" in c)
    script = sudoers.split("sh -c ", 1)[1]
    assert script.index("visudo -cf") < script.index("install -o root")
    assert any("dreamference-vllm-" in c and "systemctl restart docker" in c for c in commands)
    assert role_fleet["synced"] == [True]
    # The password went to sudo's standard input only: never into a command line.
    assert not any(SECRET in c for c in commands)
    assert ("sudo", "new-node", SECRET) in FakeSession.order
    assert "readiness" in capsys.readouterr().out


def test_a_second_run_changes_nothing(role_fleet):
    machine = FakeSession.machines["new-node"] = StandIn()
    assert provision() == 0
    first = len(machine.commands)
    assert provision() == 0
    again = machine.commands[first:]
    changes = (
        "base64 -d",
        "cat >>",
        "install.sh",
        "ling-admin swe-bench",
        "forge on",
        "apt-get",
        "key login",
    )
    assert not [c for c in again if any(change in c for change in changes)]


def test_a_dry_run_changes_nothing(role_fleet, capsys):
    machine = FakeSession.machines["new-node"] = StandIn()
    assert provision(dry_run=True) == 0
    writes = [
        c
        for c in machine.commands
        if re.search(r"base64 -d|cat >>|install\.sh|mkdir|ling-admin swe-bench|forge on|apt-get", c)
    ]
    assert writes == [] and "key login" not in machine.commands
    assert role_fleet["synced"] == []
    out = capsys.readouterr().out
    assert "would: authorise id_ed25519.pub" in out and "key-only SSH" in out and "readiness" in out


def test_password_ssh_stays_on_unless_a_separate_key_login_works(role_fleet):
    machine = FakeSession.machines["new-node"] = StandIn()
    machine.key_login = [False]
    assert provision() == 1
    assert SSHD_DROPIN_PATH not in machine.files
    assert not any("sshd -t" in c for c in machine.commands)
    # The key works before the reload but not after: password SSH is put back.
    machine = FakeSession.machines["new-node"] = StandIn()
    machine.key_login = [True, False]
    assert provision() == 1
    assert any(f"rm -f {SSHD_DROPIN_PATH} && systemctl reload ssh" in c for c in machine.commands)


def test_a_leftover_nopasswd_all_is_reported_never_removed(role_fleet, capsys):
    machine = FakeSession.machines["new-node"] = StandIn()
    machine.leftover = "/etc/sudoers.d/90-old\n"
    assert provision() == 0
    assert "/etc/sudoers.d/90-old grants NOPASSWD: ALL" in capsys.readouterr().out
    assert not any("90-old" in c for c in machine.commands if not c.startswith("sudo grep"))


def test_the_engine_role_installs_build_tools_and_reports_cuda(role_fleet):
    machine = FakeSession.machines["new-node"] = StandIn()
    assert provision(role="engine") == 0
    assert any("apt-get" in c and "cmake ninja-build" in c for c in machine.commands)
    assert not any("swe-bench" in c for c in machine.commands)


def test_the_root_and_key_commands_of_a_real_session(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs.get("input")))
            or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )
    session = FleetSession(
        "new-node", "user", tmp_path, log_path=tmp_path / "log", identity=tmp_path / "id_ed25519"
    )
    session.sudo("true", SECRET)
    argv, given = calls[-1]
    assert SECRET not in " ".join(argv) and given == SECRET + "\n"
    assert SECRET not in (tmp_path / "log").read_text()
    assert session.key_login_works()
    argv = calls[-1][0]
    for option in (
        "ControlPath=none",
        "BatchMode=yes",
        "PreferredAuthentications=publickey",
        "IdentitiesOnly=yes",
    ):
        assert option in argv
    assert argv[argv.index("-i") + 1] == str(tmp_path / "id_ed25519")


def test_the_command_line_hands_the_role_to_the_provisioner(monkeypatch):
    from dreamference.cli import main

    seen = []
    monkeypatch.setattr(NodeProvisioner, "run", lambda self: seen.append(self.options) or 0)
    monkeypatch.setattr(NodeProvisioner, "configured_model", classmethod(lambda cls: "m"))
    monkeypatch.setattr(
        "sys.argv", ["ling-admin", "node", "provision", "new-node", "--role", "engine", "--dry-run"]
    )
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    assert seen[0]["role"] == "engine" and seen[0]["dry_run"] is True


def test_a_main_node_with_no_default_key_makes_one_instead_of_failing(
    role_fleet, tmp_path, monkeypatch
):
    (tmp_path / "id_ed25519.pub").unlink()
    (tmp_path / "id_ed25519").unlink()
    made = []

    def keygen(command, **kwargs):
        made.append(command)
        (tmp_path / "id_ed25519.pub").write_text(PUBLIC_KEY + "\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", keygen)
    machine = FakeSession.machines["new-node"] = StandIn()
    assert provision() == 0
    assert made and made[0][:4] == ["ssh-keygen", "-q", "-t", "ed25519"]
    assert PUBLIC_KEY in machine.files["authorized_keys"]


def test_a_dry_run_names_the_key_it_would_make_and_makes_none(role_fleet, tmp_path, capsys):
    (tmp_path / "id_ed25519.pub").unlink()
    (tmp_path / "id_ed25519").unlink()
    FakeSession.machines["new-node"] = StandIn()
    assert provision(dry_run=True) == 0
    assert not (tmp_path / "id_ed25519").exists()
    assert "would: make id_ed25519.pub on this machine" in capsys.readouterr().out
