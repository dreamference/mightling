"""`NodeRoleProvisioner`'s steps one at a time: what each does when the machine says no.

The run and the session are stand-ins: no ssh, sudo, docker or `ssh-keygen` command runs, and every
command a step sends is recorded and answered by the first marker it contains.
"""

import json
import subprocess

import pytest

from dreamference.node import node_role_provisioner
from dreamference.node.fleet_model_plan import FleetModelPlan
from dreamference.node.fleet_probe import FleetProbe
from dreamference.node.node_provisioner import NodeProvisioner
from dreamference.node.node_role_provisioner import (
    DERIVE_FLAG,
    ENGINE,
    MIN_FREE_GIB,
    SMOKE_PASSED,
    VERIFIED,
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


# Models, the forge and key-only SSH


def test_only_the_folders_with_no_copy_anywhere_are_downloaded(monkeypatch):
    steps, run = role()
    run.plan, run.model = {"folders": ["a", "b"]}, "the-model"
    run._state = lambda host: {}
    monkeypatch.setattr(
        FleetProbe, "missing_models", classmethod(lambda cls, state, folders: folders)
    )
    monkeypatch.setattr(
        FleetModelPlan,
        "local_folders",
        classmethod(lambda cls, folders: [f for f in folders if f == "a"]),
    )
    session = Session()
    assert steps.download_missing(HOST, session) is True
    assert session.commands == ["ling-admin model download --model the-model"]
    assert run.results[HOST]["done"] == [
        "ling-admin model download --model the-model (b: no copy here)"
    ]


@pytest.fixture
def mirrors(monkeypatch):
    from dreamference.forge.forge_mirrors import ForgeMirrors

    synced = {"code": 0, "calls": 0}

    def sync(cls, quiet=False):
        synced["calls"] += 1
        return synced["code"]

    monkeypatch.setattr(ForgeMirrors, "sync", classmethod(sync))
    return synced


def test_no_forge_asks_nothing_of_the_machine(mirrors):
    steps, _ = role(no_forge=True)
    session = Session()
    assert steps.forge_mirror(HOST, session) is True
    assert session.commands == [] and mirrors["calls"] == 0


def test_a_forge_that_will_not_start_fails_and_syncs_nothing(mirrors):
    steps, run = role()
    session = Session({"mightling-forge": (1, "", ""), "forge on": (1, "", "no docker")})
    assert steps.forge_mirror(HOST, session) is False
    assert run.results[HOST]["reason"] == "no docker" and mirrors["calls"] == 0


def test_a_mirror_sync_that_did_not_complete_is_a_warning_not_a_failure(mirrors, capsys):
    mirrors["code"] = 1
    steps, run = role()
    assert steps.forge_mirror(HOST, Session()) is True
    assert "forge mirrors sync` did not complete" in capsys.readouterr().out
    assert "failed" not in run.results[HOST]


def test_a_key_only_drop_in_that_will_not_install_leaves_passwords_on():
    steps, run = role()
    session = Session(
        {INSTALL: (1, "", "sshd -t: bad option"), "passwordauthentication no": (1, "", "")}
    )
    session.key_logins = [True]
    assert steps.key_only_ssh(HOST, session) is False
    assert run.results[HOST]["reason"] == "sshd -t: bad option"
    assert session.key_logins == [], "the key was proven once and the change stopped there"


# The role's own setup


def test_build_tools_that_cannot_be_installed_fail_before_cuda_is_read():
    steps, run = role(ENGINE)
    session = Session({"command -v cmake": (1, "", ""), "apt-get": (100, "", "E: lock held")})
    assert steps.role_setup(HOST, session) is False
    assert run.results[HOST]["reason"] == "E: lock held" and "cuda" not in run.results[HOST]
    assert sent(session, "nvcc") == []


def test_night_folders_that_cannot_be_made_stop_before_swe_bench():
    steps, run = role()
    session = Session({"test -d": (1, "", ""), "mkdir -p": (1, "", "Read-only file system")})
    assert steps.role_setup(HOST, session) is False
    assert run.results[HOST]["reason"] == "Read-only file system"
    assert sent(session, "ling-admin swe-bench") == [] and sent(session, SMOKE_PASSED) == []


def test_a_failed_swe_bench_setup_fails_the_role_and_skips_the_smoke():
    steps, run = role()
    session = Session(
        {SMOKE_PASSED: (1, "", ""), "ling-admin swe-bench setup": (1, "pull failed\n", "")}
    )
    assert steps.role_setup(HOST, session) is False
    result = run.results[HOST]
    assert result["failed"] == "swe-bench setup" and result["reason"] == "pull failed"
    assert result["swe-bench setup"] is False and "swe-bench smoke" not in result
    assert sent(session, "ling-admin swe-bench smoke") == []


# Finishing and the readiness report


def test_a_model_server_that_failed_to_start_skips_the_role_setup_but_still_reports(monkeypatch):
    steps, run = role()
    run.results[HOST]["paired_record"] = True
    seen = []
    run._start_and_verify = lambda host: run.results[host].update(failed="verify")
    monkeypatch.setattr(steps, "role_setup", lambda host, session: seen.append("setup"))
    monkeypatch.setattr(steps, "report", lambda host, session: seen.append("report"))
    steps.finish(HOST, Session())
    assert seen == ["report"]


@pytest.mark.parametrize(
    ("kind", "results", "failed"),
    [
        ("night", {"verify": VERIFIED}, "role night"),
        (ENGINE, {"verify": VERIFIED}, "role engine"),
        ("night", {"swe-bench setup": True, "swe-bench smoke": True}, "model server canary"),
    ],
)
def test_a_failed_readiness_check_fails_the_run_naming_it_and_the_command_to_rerun(
    kind, results, failed
):
    steps, run = role(kind)
    run.results[HOST].update(results)
    session = Session({"df -Pk": (0, str(MIN_FREE_GIB), "")})
    steps.report(HOST, session)
    result = run.results[HOST]
    assert result["complete"] is False and result["failed"] == "readiness"
    assert result["reason"] == failed
    assert result["left"] == f"ling-admin node provision {HOST} --role {kind}"


def test_a_disk_under_the_minimum_or_unreadable_fails_readiness():
    steps, run = role(ENGINE)
    run.results[HOST].update(verify=VERIFIED, cuda="13.0")
    steps.report(HOST, Session({"df -Pk": (0, str(MIN_FREE_GIB - 1), "")}))
    assert run.results[HOST]["reason"] == "disk free"

    steps, run = role(ENGINE)
    run.results[HOST].update(verify=VERIFIED, cuda="13.0")
    steps.report(HOST, Session({"df -Pk": (0, "", "")}))
    assert run.results[HOST]["reason"] == "disk free"

    steps, run = role(ENGINE)
    run.results[HOST].update(verify=VERIFIED, cuda="13.0")
    steps.report(HOST, Session({"df -Pk": (0, str(MIN_FREE_GIB), "")}))
    assert run.results[HOST]["complete"] is True and "failed" not in run.results[HOST]
