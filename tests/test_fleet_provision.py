"""
`ling-admin node provision` and `node prepare` (specs/DREAMFERENCE_MIGHTLING_FLEET.md §14).

Nothing here reaches another machine, runs ssh, rsync or sudo, or changes this one: conftest fails
any real ssh/rsync/sudo, and every session is a stand-in that records what it was asked to do.
"""

import gzip
import hashlib
import os
import socket
import stat
import subprocess
import threading
from pathlib import Path

import pytest

from dreamference.node import fleet_session as fleet_session_module
from dreamference.node.fleet_askpass import FleetAskpass
from dreamference.node.fleet_model_plan import FleetModelPlan
from dreamference.node.fleet_probe import FleetProbe
from dreamference.node.fleet_session import FleetSession
from dreamference.node.node_browser import NodeBrowser
from dreamference.node.node_pairing import NodePairing
from dreamference.node.node_prepare import FORBIDDEN, NodePrepare
from dreamference.node.node_provisioner import NodeProvisioner

ROOT = Path(__file__).resolve().parent.parent
SECRET = "correct horse battery staple"


# -- the session ---------------------------------------------------------------------------------

def test_the_host_key_is_filed_under_the_name_given_whatever_ssh_config_says(tmp_path):
    # node-b's ~/.ssh/config entry sets `HostKeyAlias gx10-3c4d`; without this option the
    # run's known-hosts file filed the key under that alias, the fingerprint printed `?` and the
    # pairing found no key to pin (the first dry run against a real machine, 2026-10-20).
    session = FleetSession("node-b", "owner", tmp_path)
    for options in (session.options(first_contact=True), session.options()):
        assert "HostKeyAlias=node-b" in options
    other_port = FleetSession("spark-1a2b.local", "owner", tmp_path, ssh_port=2222)
    assert "HostKeyAlias=spark-1a2b.local-port-2222" in other_port.options()
    assert other_port._known_host_name() == "spark-1a2b.local-port-2222"


def test_the_session_offers_only_the_keys_named_for_the_host_never_the_whole_agent(tmp_path):
    # Seven keys in an agent end a login at sshd's MaxAuthTries before its password prompt.
    for options in (FleetSession("spark-1a2b", "owner", tmp_path).options(first_contact=True),
                    FleetSession("spark-1a2b", "owner", tmp_path).options()):
        assert "IdentitiesOnly=yes" in options


def test_session_options_keep_the_socket_private_and_forward_nothing(tmp_path):
    run_dir = FleetSession.new_run_dir()
    try:
        assert stat.S_IMODE(os.stat(run_dir).st_mode) == 0o700
        session = FleetSession("spark-1a2b", "owner", run_dir)
        first, later = session.options(first_contact=True), session.options()
        assert f"ControlPath={run_dir}/cm-spark-1a2b" in first
        assert "StrictHostKeyChecking=accept-new" in first and "StrictHostKeyChecking=yes" in later
        assert "ControlMaster=yes" in first and "ControlMaster=no" in later
        for options in (first, later):
            assert "ForwardAgent=no" in options and "ClearAllForwardings=yes" in options
            assert not any("-A" == part or part.startswith("-L") or part.startswith("-R") for part in options)
    finally:
        os.rmdir(run_dir)


def test_sudo_with_a_held_password_sends_it_on_stdin_and_never_logs_it(tmp_path, monkeypatch):
    seen = []

    def fake_run(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "done\n", "")
    monkeypatch.setattr(fleet_session_module.subprocess, "run", fake_run)
    log = tmp_path / "host.log"
    session = FleetSession("spark-1a2b", "owner", tmp_path, log_path=log)
    session.sudo("$HOME/x/ling-admin node prepare", SECRET)
    command, kwargs = seen[0]
    assert command[-1].startswith("sudo -S -p '' ")
    assert kwargs["input"] == SECRET + "\n"
    assert SECRET not in " ".join(command)
    assert kwargs.get("env") is None or SECRET not in str(kwargs["env"])
    text = log.read_text()
    assert SECRET not in text and "(input: <password>)" in text


def test_sudo_without_a_password_lets_sudo_prompt_on_a_terminal(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(fleet_session_module.subprocess, "run",
                        lambda command, **kwargs: seen.append((command, kwargs)) or subprocess.CompletedProcess(command, 0, None, None))
    FleetSession("h", "u", tmp_path).sudo("ling-admin node prepare", None)
    command, kwargs = seen[0]
    assert "-t" in command and command[-1] == "sudo ling-admin node prepare"
    assert kwargs["input"] is None and kwargs["capture_output"] is False


def test_docker_commands_after_prepare_go_through_sg(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(fleet_session_module.subprocess, "run",
                        lambda command, **kwargs: seen.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    FleetSession("h", "u", tmp_path).run_admin(["searxng", "start"], docker=True)
    assert seen[0][-1].startswith("sg docker -c ")
    assert "searxng start" in seen[0][-1]


def test_opening_the_master_hands_ssh_the_askpass_script_not_the_password(tmp_path, monkeypatch):
    seen = []

    def fake_run(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(fleet_session_module.subprocess, "run", fake_run)
    askpass = FleetAskpass(tmp_path, {"h": SECRET})
    session = FleetSession("h", "u", tmp_path)
    session.askpass_env = askpass.environment("h")
    assert session.open()
    command, kwargs = seen[0]
    assert "-f" in command and "-N" in command
    assert kwargs["env"]["SSH_ASKPASS_REQUIRE"] == "force"
    assert SECRET not in str(kwargs["env"]) and SECRET not in " ".join(command)
    # `-f` keeps the master running with what it inherited: never a pipe this process waits on.
    assert kwargs["stdout"] == subprocess.DEVNULL and kwargs.get("capture_output") is None


# -- askpass ---------------------------------------------------------------------------------------

def test_askpass_answers_its_own_host_over_the_socket(tmp_path, monkeypatch, capsys):
    run_dir = Path(FleetSession.new_run_dir())
    askpass = FleetAskpass(run_dir, {"spark-1": SECRET})
    askpass.start("/usr/bin/false")
    try:
        assert stat.S_IMODE(os.stat(askpass.socket_path).st_mode) == 0o600
        assert "node askpass" in askpass.script_path.read_text()
        for key, value in askpass.environment("spark-1").items():
            monkeypatch.setenv(key, value)
        assert FleetAskpass.ask() == 0
        assert capsys.readouterr().out == SECRET + "\n"
        monkeypatch.setenv("MIGHTLING_ASKPASS_HOST", "someone-else")
        assert FleetAskpass.ask() == 1
        assert capsys.readouterr().out == ""
    finally:
        askpass.stop()
        os.rmdir(run_dir)


def test_askpass_without_a_run_prints_nothing(monkeypatch, capsys):
    monkeypatch.delenv("MIGHTLING_ASKPASS_SOCKET", raising=False)
    assert FleetAskpass.ask() == 1
    assert capsys.readouterr().out == ""


# -- node prepare ----------------------------------------------------------------------------------

@pytest.fixture
def machine(monkeypatch):
    """A machine whose readings the test sets; nothing on the real one is read."""
    state = {"host": [], "docker": None, "linger": False, "avahi": None, "sandbox": None, "telemetry": None}
    from dreamference.node import node_prepare
    monkeypatch.setattr(NodePrepare, "_host_steps", classmethod(lambda cls: list(state["host"])))
    monkeypatch.setattr(NodePrepare, "_docker_step", classmethod(lambda cls, user: state["docker"]))
    monkeypatch.setattr(NodePrepare, "_avahi_step", classmethod(lambda cls, user: state["avahi"]))
    monkeypatch.setattr(NodePrepare, "_sandbox_step", classmethod(lambda cls: state["sandbox"]))
    monkeypatch.setattr(NodePrepare, "_telemetry_step", classmethod(lambda cls: state["telemetry"]))
    monkeypatch.setattr(node_prepare.Path, "exists", lambda self: state["linger"] if "linger" in str(self) else Path.is_file(self))
    return state


def test_prepare_refuses_without_root(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert NodePrepare.run() is False
    assert "sudo ling-admin node prepare" in capsys.readouterr().out


def test_prepare_refuses_without_sudo_user(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.delenv("SUDO_USER", raising=False)
    assert NodePrepare.run() is False
    assert "SUDO_USER" in capsys.readouterr().out


def test_prepare_on_a_ready_machine_changes_nothing(machine, monkeypatch):
    machine["linger"] = True
    ran = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "owner")
    monkeypatch.setattr(NodePrepare, "_execute", classmethod(lambda cls, command: ran.append(command) or True))
    assert NodePrepare.steps("owner") == []
    assert NodePrepare.run() is True and ran == []


def test_prepare_runs_exactly_the_missing_steps(machine):
    machine["docker"] = {"name": "Docker without sudo", "why": "x", "commands": [["usermod", "-aG", "docker", "owner"]]}
    machine["telemetry"] = {"name": "NVIDIA's telemetry", "why": "x",
                            "commands": [["systemctl", "disable", "--now", "nvidia-dgx-telemetry"]]}
    names = [step["name"] for step in NodePrepare.steps("owner")]
    assert names == ["Docker without sudo", "lingering", "NVIDIA's telemetry"]
    linger = NodePrepare.steps("owner")[1]
    assert linger["commands"] == [["loginctl", "enable-linger", "owner"]]


def test_the_real_steps_never_touch_ssh_the_network_accounts_or_apt(monkeypatch):
    # Every reading says "missing", so every step that has commands produces them.
    from dreamference.node import node_prepare
    from dreamference.vllm_server.sandbox_prerequisite import SandboxPrerequisite
    monkeypatch.setattr(NodePrepare, "_host_steps", classmethod(lambda cls: []))
    monkeypatch.setattr(node_prepare.grp, "getgrnam", lambda name: type("G", (), {"gr_mem": []})())
    monkeypatch.setattr(NodePrepare, "_primary_group", classmethod(lambda cls, user: "owner"))
    monkeypatch.setattr(NodePrepare, "_systemctl", classmethod(lambda cls, verb, unit: "enabled"))
    monkeypatch.setattr(NodePrepare, "_profile_loaded", classmethod(lambda cls: False))
    monkeypatch.setattr(SandboxPrerequisite, "fix_commands",
                        classmethod(lambda cls: [["install", "-m", "644", "/tmp/p", "/etc/apparmor.d/puffin-bwrap"],
                                                 ["apparmor_parser", "-r", "/etc/apparmor.d/puffin-bwrap"]]))
    monkeypatch.setattr(node_prepare.pwd, "getpwnam", lambda name: type("P", (), {"pw_uid": 4242, "pw_gid": 4242})())
    steps = NodePrepare.steps("owner")
    names = [step["name"] for step in steps]
    assert "NVIDIA's telemetry" in names and "lingering" in names and "Docker without sudo" in names
    for step in steps:
        for command in step.get("commands", []):
            joined = " ".join(command)
            assert not any(word in joined for word in FORBIDDEN), joined


def test_prepare_refuses_a_forbidden_command_even_if_asked(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    assert NodePrepare._execute(["systemctl", "restart", "sshd"]) is False
    assert called == [] and "never runs" in capsys.readouterr().out


# -- the probe --------------------------------------------------------------------------------------

def test_the_probe_reads_and_never_writes():
    script = FleetProbe.script(["models--a--b"], ["lmsysorg/sglang@sha256:abc", "dreamference-vllm:1"])
    for writer in ("rm ", "mv ", "cp ", " > /", "usermod", "systemctl disable", "docker pull", "docker load"):
        assert writer not in script
    assert "model:models--a--b" in script and "image:dreamference-vllm:1" in script


def test_a_folder_or_image_name_with_shell_characters_is_only_a_name(tmp_path):
    folder, image = "m; touch PWN #", "i$(touch PWN)"
    body = FleetProbe.script([folder], [image])
    lines = [ln for ln in body.splitlines() if ln.startswith(("[ -d", "( docker"))]
    script = "\n".join(
        [
            "v() { printf '%s=%s\\n' \"$1\" \"$2\"; }",
            f"hub={tmp_path}",
            "docker() { return 1; }",
            "sg() { return 1; }",
            *lines,
        ]
    )

    done = subprocess.run(["sh", "-c", script], cwd=tmp_path, capture_output=True, text=True)

    assert not (tmp_path / "PWN").exists()
    assert done.stdout.splitlines() == [f"model:{folder}=0", f"image:{image}=0"]


def test_the_probe_output_is_parsed_and_judged():
    state = FleetProbe.parse("dgx=1\ngpu=NVIDIA GB10\ndocker_group=1\nlinger=1\navahi_file=1\n"
                             "sandbox_profile=0\nuserns_restricted=1\ntelemetry=enabled\nmodel:m1=1\nmodel:m2=0\n"
                             "image:i1=0\nnoise line\n")
    assert FleetProbe.is_gb10(state)
    assert not FleetProbe.prepared(state)
    assert FleetProbe.missing_models(state, ["m1", "m2"]) == ["m2"]
    assert FleetProbe.missing_images(state, ["i1"]) == ["i1"]
    state.update(sandbox_profile="1", telemetry="disabled")
    assert FleetProbe.prepared(state)
    assert not FleetProbe.is_gb10({"dgx": "0", "gpu": "NVIDIA H100"})


# -- the model plan ------------------------------------------------------------------------------

def test_registry_images_are_pulled_and_local_tags_copied():
    assert FleetModelPlan.from_registry("lmsysorg/sglang@sha256:" + "a" * 64)
    assert FleetModelPlan.from_registry("docker.io/searxng/searxng:latest")
    assert not FleetModelPlan.from_registry("dreamference-vllm-dflash:0.23.0-aeon-dense1")


def test_the_plan_for_each_offered_model(monkeypatch):
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
    monkeypatch.setattr(FleetModelPlan, "_embedding_folder", classmethod(lambda cls: "models--nomic-ai--nomic-embed-text-v1.5"))
    for key, spec in ModelMatrixRegistry.MATRIX.items():
        if getattr(spec, "is_diffusion", False):
            continue
        plan = FleetModelPlan.plan(key)
        assert plan["folders"][0].startswith("models--")
        assert "models--nomic-ai--nomic-embed-text-v1.5" in plan["folders"]
        assert plan["image"] in plan["copy_images"] + plan["pull_images"]
        assert "docker.io/searxng/searxng:latest" in plan["pull_images"]


def test_insufficient_disk_is_refused_before_any_copy():
    assert FleetModelPlan.fits(100, 50 * 1024 ** 3)
    assert not FleetModelPlan.fits(60, 50 * 1024 ** 3)


def test_a_node_short_of_disk_that_has_every_model_is_not_refused():
    # Nothing to copy needs no margin: a full node that has the model can still be updated.
    assert FleetModelPlan.fits(5, 0)
    assert FleetModelPlan.fits(0, 0)


# -- discovery -------------------------------------------------------------------------------------

def avahi_line(service, name, host, address):
    return f'=;wlp1s0;IPv4;{name};{service};local;{host};{address};22;"x=1"'


def test_discovery_lists_new_gb10s_and_leaves_out_nodes_and_others(monkeypatch):
    ssh = "\n".join([avahi_line("_ssh._tcp", "spark-1a2b", "spark-1a2b.local", "10.0.0.5"),
                     avahi_line("_ssh._tcp", "gx10-1a2b", "gx10-1a2b.local", "10.0.0.2"),
                     avahi_line("_ssh._tcp", "laptop", "laptop.local", "10.0.0.9")])
    nodes = [{"name": "gx10-1a2b", "host": "gx10-1a2b.local", "address": "10.0.0.2"}]
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: nodes))
    monkeypatch.setattr(NodeBrowser, "browse_service",
                        classmethod(lambda cls, service, timeout=6: NodeBrowser.parse(ssh, service)))
    found = NodeBrowser.unprovisioned()
    assert [entry["host"] for entry in found] == ["spark-1a2b.local"]
    assert [entry["host"] for entry in NodeBrowser.unprovisioned(match="^lap")] == ["laptop.local"]


# -- pairing -----------------------------------------------------------------------------------------

def test_a_host_key_different_from_the_pinned_one_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(NodePairing, "known_hosts", classmethod(lambda cls: tmp_path / "known_hosts"))
    assert NodePairing.pin_host_keys("abcdef12", ["ssh-ed25519 AAAAone"])
    assert NodePairing.pin_host_keys("abcdef12", ["ssh-ed25519 AAAAone"])
    assert (tmp_path / "known_hosts").read_text().count("mightling-node-abcdef12") == 1
    assert not NodePairing.pin_host_keys("abcdef12", ["ssh-ed25519 AAAAtwo"])
    assert "differs" in capsys.readouterr().out


def test_node_add_falls_back_to_a_login_when_no_browse_finds_it(monkeypatch):
    monkeypatch.setattr(NodePairing, "discover", classmethod(lambda cls, name: None))
    called = []
    monkeypatch.setattr(NodePairing, "add_by_login",
                        classmethod(lambda cls, address, user, port: called.append((address, user, port)) or True))
    assert NodePairing.add("10.0.0.5", user="owner")
    assert called == [("10.0.0.5", "owner", 22)]


# -- a whole run, against stand-in machines ----------------------------------------------------------

class StandIn:
    """A machine as the provisioner sees it through its session: readings and recorded commands."""

    def __init__(self, ready=False, gb10=True):
        self.commands = []
        self.state = {"dgx": "1" if gb10 else "0", "gpu": "NVIDIA GB10" if gb10 else "x",
                      "admin": "0", "bundle": "", "docker_group": "0", "linger": "0", "avahi_file": "0",
                      "advertised": "0", "sandbox_profile": "0", "userns_restricted": "1", "telemetry": "enabled",
                      "free_gb": "500", "hub": "/home/user/.cache/huggingface/hub", "node_id": "feedface1234"}
        self.prepare_fails = False
        self.copied = self.pulled = False
        if ready:
            self.become_ready()
            self.state["advertised"] = "1"
            self.copied = self.pulled = True

    def become_ready(self):
        self.state.update(admin="1", bundle="this-test", docker_group="1", linger="1", avahi_file="1",
                          sandbox_profile="1", telemetry="disabled")

    def probe_output(self, folders, images):
        lines = [f"{k}={v}" for k, v in self.state.items()]
        lines += [f"model:{f}={int(self.copied)}" for f in folders] + [f"image:{i}={int(self.pulled)}" for i in images]
        return "\n".join(lines) + "\n"


class FakeSession:
    machines = {}
    order = []

    def __init__(self, host, user, run_dir, ssh_port=22, log_path=None):
        self.host, self.user, self.ssh_port = host, user, ssh_port
        self.machine = FakeSession.machines[host]
        self.askpass_env = None

    def open(self):
        FakeSession.order.append(("open", self.host))
        return True

    def close(self):
        pass

    def host_key_fingerprint(self):
        return "abc"

    def host_key_lines(self):
        return ["ssh-ed25519 AAAAkey"]

    def run(self, script, input_text=None, terminal=False, secret_input=False, timeout=None):
        if "dgx-release" in script:
            folders = [line.split("v model:")[1].split()[0] for line in script.splitlines() if "v model:" in line]
            images = [line.split("v image:")[1].split()[0] for line in script.splitlines() if "v image:" in line]
            return subprocess.CompletedProcess(script, 0, self.machine.probe_output(folders, images), "")
        self.machine.commands.append(script)
        FakeSession.order.append(("run", self.host, script))
        if "install.sh" in script:
            self.machine.state.update(admin="1", bundle="this-test")
        if "node enable" in script:
            self.machine.state.update(advertised="1")
        if "docker pull" in script:
            self.machine.pulled = True
        if "searxng start" in script:
            self.machine.state["searxng"] = "true"
        if "main-model set" in script:
            self.machine.state["model"] = script.split()[-1]
        if "host check" in script:
            return subprocess.CompletedProcess(script, 0 if self.machine.state["linger"] == "1" else 1, "", "")
        return subprocess.CompletedProcess(script, 0, "", "")

    def run_admin(self, arguments, timeout=None, docker=False):
        return self.run("ling-admin " + " ".join(arguments))

    def sudo(self, line, password):
        self.machine.commands.append(f"sudo {line}")
        FakeSession.order.append(("sudo", self.host, password))
        if "python3-venv" in line:
            self.machine.state["venv"] = "1"
            return subprocess.CompletedProcess(line, 0, "", "")
        if self.machine.prepare_fails:
            return subprocess.CompletedProcess(line, 1, "boom", "")
        self.machine.become_ready()
        return subprocess.CompletedProcess(line, 0, "", "")

    def rsync(self, source, destination, **options):
        self.machine.commands.append(f"rsync {source} {destination}")
        if "models--" in str(source):
            self.machine.copied = True
        return subprocess.CompletedProcess([], 0, "", "")

    def pipe_into(self, producer, remote_line):
        self.machine.commands.append(f"pipe {producer}")
        return 0


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    from dreamference.node import node_provisioner
    FakeSession.machines, FakeSession.order = {}, []
    monkeypatch.setattr(node_provisioner, "FleetSession", FakeSession)
    monkeypatch.setattr(FakeSession, "new_run_dir", classmethod(lambda cls: tmp_path / "run"), raising=False)
    monkeypatch.setattr(node_provisioner.FleetSession, "with_docker_group", staticmethod(lambda line: f"sg docker -c '{line}'"), raising=False)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "VERSION").write_text("this-test\n")
    monkeypatch.setattr(node_provisioner.FleetBundle, "build", classmethod(lambda cls, source="this": bundle))
    monkeypatch.setattr(FleetModelPlan, "plan", classmethod(lambda cls, key: {
        "folders": ["models--org--m"], "image": "lmsysorg/sglang@sha256:x",
        "copy_images": [], "pull_images": ["lmsysorg/sglang@sha256:x", "docker.io/searxng/searxng:latest"]}))
    monkeypatch.setattr(FleetModelPlan, "local_folders", classmethod(lambda cls, folders: [tmp_path / f for f in folders]))
    monkeypatch.setattr(FleetModelPlan, "bytes_needed", classmethod(lambda cls, folders: 10 * 1024 ** 3 * len(folders)))
    paired = {}
    monkeypatch.setattr(NodePairing, "find", classmethod(lambda cls, name, browse=True: paired.get(name)))

    def pair(cls, session):
        record = {"node": session.machine.state["node_id"] + session.host, "name": session.host,
                  "address": session.host, "user": session.user}
        paired[record["node"]] = record
        return record
    monkeypatch.setattr(NodePairing, "pair_over_session", classmethod(pair))
    monkeypatch.setattr(NodePairing, "_save", classmethod(lambda cls, record: None))
    monkeypatch.setattr(NodePairing, "run", classmethod(
        lambda cls, record, request, capture=True, input_text=None: subprocess.CompletedProcess([], 0, '{"model_port": 8000}', "")))
    monkeypatch.setattr(NodeProvisioner, "_served_model", lambda self, host, port: "qwen")
    monkeypatch.setattr(NodeProvisioner, "_verify", lambda self, host, port, model: "ok")
    monkeypatch.setattr(NodeProvisioner, "configured_model", classmethod(lambda cls: "qwen3.8-27b-nvfp4-dflash2"))
    monkeypatch.setattr(NodeProvisioner, "_admin_executable", classmethod(lambda cls: "/usr/bin/false"))
    monkeypatch.setattr(NodeProvisioner, "print_summary", lambda self: None)
    # A stand-in's name resolves as given; no real lookup (mDNS) from a test.
    monkeypatch.setattr(NodeProvisioner, "_resolves", classmethod(lambda cls, name: name in FakeSession.machines))
    asked = []
    monkeypatch.setattr(node_provisioner.getpass, "getpass", lambda prompt: asked.append(prompt) or SECRET)
    monkeypatch.setattr(node_provisioner.getpass, "getuser", lambda: "owner")

    class Askpass:
        def __init__(self, run_dir, passwords):
            self.passwords = passwords

        def start(self, admin):
            FakeSession.order.append(("askpass",))

        def environment(self, host):
            return {"MIGHTLING_ASKPASS_HOST": host}

        def stop(self):
            pass
    monkeypatch.setattr(node_provisioner, "FleetAskpass", Askpass)
    monkeypatch.setattr(node_provisioner.shutil, "rmtree", lambda *a, **k: None)
    return {"asked": asked, "paired": paired}


def provision(hosts, **options):
    return NodeProvisioner(hosts, options).run()


def test_one_new_machine_goes_all_the_way(fleet):
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"]) == 0
    commands = FakeSession.machines["spark-1"].commands
    assert any("install.sh --from" in c and "--no-host-setup" in c and "--no-advertise" in c and "--no-model" in c
               for c in commands)
    assert any(c.startswith("sudo ") and "node prepare" in c for c in commands)
    assert any("node enable --no-web" in c for c in commands)
    assert any("docker pull lmsysorg/sglang@sha256:x" in c for c in commands)
    assert any("searxng start" in c for c in commands)
    # The default model needs no assignment: an unset config already means it.
    assert not any("main-model set" in c for c in commands)
    # One host: ling-admin never asks for (or holds) a password; ssh and sudo prompt themselves.
    assert fleet["asked"] == []
    assert ("sudo", "spark-1", None) in FakeSession.order


def test_with_several_machines_every_question_comes_before_any_install(fleet):
    FakeSession.machines = {"spark-1": StandIn(), "spark-2": StandIn()}
    assert provision(["spark-1", "spark-2"]) == 0
    assert len(fleet["asked"]) == 1  # one password for all, by default
    first_install = next(i for i, entry in enumerate(FakeSession.order) if entry[0] == "run" and "install.sh" in entry[2])
    opens = [i for i, entry in enumerate(FakeSession.order) if entry[0] == "open"]
    assert len(opens) == 2 and max(opens) < first_install
    assert ("sudo", "spark-1", SECRET) in FakeSession.order


def test_per_host_password_asks_each_machine(fleet):
    FakeSession.machines = {"spark-1": StandIn(), "spark-2": StandIn()}
    assert provision(["spark-1", "spark-2"], per_host_password=True) == 0
    assert len(fleet["asked"]) == 2


def test_a_second_run_on_a_finished_node_changes_nothing(fleet):
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"]) == 0
    FakeSession.machines["spark-1"].commands.clear()
    assert provision(["spark-1"]) == 0
    changing = [c for c in FakeSession.machines["spark-1"].commands if "host check" not in c]
    assert changing == []


def test_a_failure_stops_that_machine_only_and_the_run_reports_it(fleet, capsys):
    broken, fine = StandIn(), StandIn()
    broken.prepare_fails = True
    FakeSession.machines = {"spark-1": broken, "spark-2": fine}
    assert provision(["spark-1", "spark-2"]) == 1
    assert not any("node enable" in c for c in broken.commands)
    assert any("node enable" in c for c in fine.commands)
    assert "spark-1: node prepare" in capsys.readouterr().out


def test_a_machine_that_is_not_a_gb10_is_left_untouched(fleet):
    FakeSession.machines = {"box": StandIn(gb10=False)}
    assert provision(["box"]) == 1
    assert FakeSession.machines["box"].commands == []


def test_a_dry_run_reads_and_changes_nothing(fleet, capsys):
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"], dry_run=True) == 0
    assert FakeSession.machines["spark-1"].commands == []
    out = capsys.readouterr().out
    assert "would change" in out and "node prepare" in out


def test_another_model_is_assigned_there(fleet, vllm_recipes):
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"], model="test-vllm-nvfp4") == 0
    assert any("main-model set test-vllm-nvfp4" in c for c in FakeSession.machines["spark-1"].commands)


def test_web_is_off_unless_asked(fleet):
    FakeSession.machines = {"spark-1": StandIn()}
    provision(["spark-1"], web=True)
    commands = FakeSession.machines["spark-1"].commands
    assert any(c.endswith("node enable") for c in commands)
    # The web UI is `ling web`, which `node enable` puts on the LAN; nothing installs Onyx (ASK §19).
    assert not any(word in c for c in commands for word in ("chat start", "configure", "ling start", "onyx"))


def test_a_dry_run_with_web_names_ling_web(fleet, capsys):
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"], dry_run=True, web=True) == 0
    out = capsys.readouterr().out
    assert "ling web" in out and "--no-web" not in out and "chat start" not in out


def test_python3_venv_is_installed_as_root_before_the_install_needs_it(fleet):
    machine = StandIn()
    machine.state["venv"] = "0"
    FakeSession.machines = {"spark-1": machine}
    assert provision(["spark-1"]) == 0
    venv = next(i for i, c in enumerate(machine.commands) if "apt-get" in c and "python3-venv" in c)
    install = next(i for i, c in enumerate(machine.commands) if "install.sh --from" in c)
    prepare = next(i for i, c in enumerate(machine.commands) if "node prepare" in c)
    assert machine.commands[venv].startswith("sudo ") and venv < install < prepare
    FakeSession.machines["spark-1"].commands.clear()
    assert provision(["spark-1"]) == 0
    assert not any("python3-venv" in c for c in machine.commands)


def test_a_bare_name_that_does_not_resolve_is_reached_by_mdns(fleet):
    FakeSession.machines = {"spark-1a2b.local": StandIn()}
    assert provision(["spark-1a2b"]) == 0
    assert any("install.sh --from" in c for c in FakeSession.machines["spark-1a2b.local"].commands)


def test_all_reaches_each_paired_node_by_its_id_never_by_its_remembered_address(fleet, monkeypatch, capsys):
    records = [{"node": "aaaa1111", "name": "spark-old", "address": "192.0.2.7", "user": "engine"},
               {"node": "bbbb2222", "name": "spark-gone", "address": "192.0.2.8", "user": "owner"}]
    monkeypatch.setattr(NodePairing, "paired", classmethod(lambda cls: records))
    resolved = []
    found = {"aaaa1111": "198.51.100.4"}
    monkeypatch.setattr(NodePairing, "resolve", classmethod(
        lambda cls, record: resolved.append(record["node"]) or found.get(record["node"])))
    FakeSession.machines = {"198.51.100.4": StandIn(), "192.0.2.7": StandIn(), "192.0.2.8": StandIn()}
    sessions = []
    original = FakeSession.__init__

    def record_init(self, host, user, run_dir, ssh_port=22, log_path=None):
        sessions.append((host, user))
        original(self, host, user, run_dir, ssh_port, log_path)
    monkeypatch.setattr(FakeSession, "__init__", record_init)
    assert provision([], all=True) == 1
    assert resolved == ["aaaa1111", "bbbb2222"]
    assert sessions == [("198.51.100.4", "engine")]
    assert FakeSession.machines["192.0.2.7"].commands == [] and FakeSession.machines["192.0.2.8"].commands == []
    assert "spark-gone: connect: off the network" in capsys.readouterr().out


def test_a_dry_run_of_a_finished_paired_node_names_only_what_differs(fleet, capsys):
    machine = StandIn(ready=True)
    machine.state["searxng"] = "true"
    FakeSession.machines = {"spark-1": machine}
    fleet["paired"][machine.state["node_id"]] = {"node": machine.state["node_id"]}
    assert provision(["spark-1"], dry_run=True, no_start=True) == 0
    out = capsys.readouterr().out
    assert machine.commands == []
    absent_words = ("install Mightling", "python3-venv", "main-model set", "pair (", "copy models",
                    "image ", "start the")
    for absent in absent_words:
        assert absent not in out
    assert "replace Mightling this-test" in out


def test_the_bundle_carries_every_binary_install_sh_installs():
    from dreamference.node.fleet_bundle import OPTIONAL_BINARIES, REQUIRED_BINARIES
    script = (ROOT / "install.sh").read_text()
    names = set()
    for line in script.splitlines():
        line = line.strip()
        for prefix in ('REQUIRED="', 'OPTIONAL="', '[ "$(uname -s)" = Linux ] && OPTIONAL="$OPTIONAL '):
            if line.startswith(prefix):
                names |= set(line[len(prefix):].rstrip('"').split())
    assert names and names == set(REQUIRED_BINARIES) | set(OPTIONAL_BINARIES)


def test_an_unknown_model_is_refused_before_connecting(fleet):
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"], model="no-such-model") == 1
    assert FakeSession.order == []


# -- install.sh --from -------------------------------------------------------------------------------

TARGET = "aarch64-unknown-linux-gnu"


def make_bundle(folder, corrupt=None):
    folder.mkdir()
    sums = []
    for name in ("ling", "codex-code-mode-host", "ling-search", "ling-fetch"):
        archive = gzip.compress(f"#!/bin/sh\necho {name} from the bundle\n".encode())
        sums.append(f"{hashlib.sha256(archive).hexdigest()}  {name}-{TARGET}.gz")
        (folder / f"{name}-{TARGET}.gz").write_bytes(archive + (b"x" if name == corrupt else b""))
    (folder / f"ling-{TARGET}.sha256sums").write_text("\n".join(sums) + "\n")
    (folder / "VERSION").write_text("this-abc123\n")
    return folder


def run_install_from(home, bundle):
    fake_bin = Path(home) / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    for name, script in (("uname", 'case "$1" in -s) echo Linux;; -m) echo aarch64;; *) echo Linux;; esac'),
                         ("nvidia-smi", 'echo "Other GPU"'),
                         ("curl", "echo curl must not run >&2; exit 99")):
        (fake_bin / name).write_text(f"#!/bin/sh\n{script}\n")
        (fake_bin / name).chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{fake_bin}:/usr/bin:/bin"}
    return subprocess.run(["bash", str(ROOT / "install.sh"), "--from", str(bundle), "--role", "client"],
                          env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, check=False)


def test_install_from_a_bundle_uses_no_network_and_records_the_version(tmp_path):
    bundle = make_bundle(tmp_path / "bundle")
    result = run_install_from(tmp_path, bundle)
    assert result.returncode == 0, result.stderr
    assert "curl must not run" not in result.stderr
    link = tmp_path / ".local" / "bin" / "ling"
    assert subprocess.run([str(link)], capture_output=True, text=True).stdout == "ling from the bundle\n"
    assert (tmp_path / ".local/share/dreamference/mightling/VERSION").read_text().strip() == "this-abc123"


def test_a_bundle_file_that_fails_its_checksum_installs_nothing(tmp_path):
    bundle = make_bundle(tmp_path / "bundle", corrupt="ling-search")
    result = run_install_from(tmp_path, bundle)
    assert result.returncode == 1 and "does not match its checksum" in result.stderr
    assert not (tmp_path / ".local/share/dreamference/mightling/bin/ling").exists()


def test_install_help_documents_from_and_no_host_setup():
    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True, check=False)
    assert "--from <dir>" in result.stdout and "--no-host-setup" in result.stdout


# -- a machine at another site, reached through the remote-access overlay ----------------------------


def test_a_bare_name_resolves_in_the_overlay_before_mdns(fleet):
    FakeSession.machines = {"house-1.netbird.selfhosted": StandIn()}
    assert provision(["house-1"]) == 0
    assert "house-1.netbird.selfhosted" in {entry[1] for entry in FakeSession.order if len(entry) > 1}


def test_a_full_overlay_name_is_used_as_typed(fleet):
    FakeSession.machines = {"house-1.netbird.selfhosted": StandIn()}
    assert provision(["house-1.netbird.selfhosted"]) == 0


def test_a_paired_node_with_an_overlay_name_is_reached_by_that_name_with_no_browse(monkeypatch):
    record = {"node": "ab12", "name": "house-1", "address": "stale", "user": "owner",
              "overlay_name": "house-1.netbird.selfhosted"}
    monkeypatch.setattr(NodePairing, "_name_resolves", classmethod(lambda cls, name: True))
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: pytest.fail("no mDNS")))
    command = NodePairing.ssh_command(record, "info")
    assert command[-2] == "owner@house-1.netbird.selfhosted"
    assert NodePairing.is_overlay_name("House-1.netbird.selfhosted.")
    assert not NodePairing.is_overlay_name("spark-1.local")


def test_an_overlay_name_that_does_not_resolve_falls_back_to_the_lan_browse(monkeypatch):
    record = {"node": "ab12", "name": "house-1", "address": "x", "user": "owner",
              "overlay_name": "house-1.netbird.selfhosted"}
    monkeypatch.setattr(NodePairing, "_name_resolves", classmethod(lambda cls, name: False))
    monkeypatch.setattr(NodePairing, "resolve_wait_s", 0.0)
    NodePairing.forget_resolved()
    monkeypatch.setattr(NodeBrowser, "browse", classmethod(lambda cls, timeout=6: [
        {"name": "house-1", "node": "ab12", "address": "192.168.1.9", "port": "8000"}]))
    monkeypatch.setattr(NodePairing, "_save", classmethod(lambda cls, record: None))
    assert NodePairing.resolve(record) == "192.168.1.9"


def _session_with(tmp_path, results, sleeps):
    session = FleetSession("house-1.netbird.selfhosted", "owner", tmp_path)
    calls = []

    def spawn(command, capture, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, results.pop(0), "", "")

    session._spawn = spawn
    session._log = lambda *args, **kwargs: None
    return session, calls


def test_a_model_copy_that_loses_its_connection_resumes_and_a_real_failure_does_not(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(fleet_session_module.time, "sleep", lambda seconds: None)
    sleeps = []
    session, calls = _session_with(tmp_path, [255, 30, 0], sleeps)
    done = session.rsync(tmp_path, "~/.cache/huggingface/hub/models--x", attempts=5, wait_s=7)
    assert done.returncode == 0 and len(calls) == 3
    command = calls[0]
    assert "--partial" in command and "--append-verify" in command and "--timeout=120" in command
    assert "ServerAliveInterval=15" in command[command.index("-e") + 1]
    session, calls = _session_with(tmp_path, [23, 0], sleeps)  # partial transfer: not a drop
    assert session.rsync(tmp_path, "~/x", attempts=5).returncode == 23 and len(calls) == 1
    session, calls = _session_with(tmp_path, [255] * 3, sleeps)
    assert session.rsync(tmp_path, "~/x", attempts=3).returncode == 255 and len(calls) == 3


# -- the release and checkout bundle (FleetBundle) ---------------------------------------------------


def test_bundle_target_and_expected_version_across_install_modes(tmp_path, monkeypatch):
    from dreamference.node import fleet_bundle as bundle_module
    from dreamference.node.fleet_bundle import FleetBundle
    from dreamference.runner import codex_branded_builder as builder_module

    monkeypatch.setattr(bundle_module.platform, "machine", lambda: "arm64")
    assert FleetBundle.target() == "aarch64-unknown-linux-gnu"
    monkeypatch.setattr(bundle_module.platform, "machine", lambda: "amd64")
    assert FleetBundle.target() == "x86_64-unknown-linux-gnu"

    install_dir = tmp_path / "install"
    (install_dir / "bin").mkdir(parents=True)
    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(
        builder_module.CodexBrandedBuilder, "has_source", classmethod(lambda cls: True)
    )
    assert FleetBundle.expected_version("this") is None

    (install_dir / "bin" / "ling").write_bytes(b"ling-bin")
    expected = FleetBundle.expected_version("this")
    assert expected is not None and expected.startswith("this-") and len(expected) == 17

    monkeypatch.setattr(
        builder_module.CodexBrandedBuilder, "has_source", classmethod(lambda cls: False)
    )
    release_ver = FleetBundle.expected_version("this")
    assert release_ver is not None and release_ver.startswith("v")
    assert FleetBundle.expected_version("release=v1.7.2") == "v1.7.2"
    assert FleetBundle.expected_version("release") is None
    assert FleetBundle.version(tmp_path / "nonexistent") == "bundle"


def test_bundle_from_this_packs_binaries_builds_wheel_and_reuses_cache(
    tmp_path, monkeypatch, capsys
):
    from dreamference.node import fleet_bundle as bundle_module
    from dreamference.node.fleet_bundle import FleetBundle
    from dreamference.runner import codex_branded_builder as builder_module

    install_dir = tmp_path / "install"
    bin_dir = install_dir / "bin"
    bin_dir.mkdir(parents=True)
    bundle_root = tmp_path / "bundles"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(builder_module, "INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(builder_module, "REPO_ROOT", str(repo_root))
    monkeypatch.setattr(bundle_module, "BUNDLE_ROOT", str(bundle_root))
    monkeypatch.setattr(
        builder_module.CodexBrandedBuilder, "has_source", classmethod(lambda cls: True)
    )

    assert FleetBundle.build("bogus") is None
    assert "--from is `this` or `release[=X.Y.Z]`" in capsys.readouterr().out

    assert FleetBundle.build("this") is None
    assert "ling, codex-code-mode-host not installed" in capsys.readouterr().out

    (bin_dir / "ling").write_bytes(b"ling-binary")
    (bin_dir / "codex-code-mode-host").write_bytes(b"host-binary")
    (bin_dir / "ling-search").write_bytes(b"search-binary")

    monkeypatch.setattr(
        bundle_module.SafeGit,
        "run",
        classmethod(lambda cls, *a, **k: subprocess.CompletedProcess([], 0, "abc123\n", "")),
    )
    wheel_calls = []

    def fail_wheel(argv, **kwargs):
        wheel_calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "pip failed")

    monkeypatch.setattr(bundle_module.subprocess, "run", fail_wheel)
    assert FleetBundle.from_this() is None
    assert "Could not build a wheel" in capsys.readouterr().out

    def make_wheel(argv, **kwargs):
        wheel_calls.append(argv)
        staging = Path(argv[argv.index("-w") + 1])
        (staging / "dreamference-1.7.2-py3-none-any.whl").write_bytes(b"wheel")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bundle_module.subprocess, "run", make_wheel)
    monkeypatch.setattr(
        bundle_module.SafeGit,
        "run",
        classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(OSError("no git"))),
    )
    built = FleetBundle.build("this")
    assert built is not None and built.is_dir()
    assert len(wheel_calls) == 2
    assert FleetBundle.version(built).startswith("this-")
    target = FleetBundle.target()
    with gzip.open(built / f"ling-{target}.gz", "rb") as handle:
        assert handle.read() == b"ling-binary"
    sums = (built / f"ling-{target}.sha256sums").read_text(encoding="utf-8")
    assert f"ling-{target}.gz" in sums and f"ling-search-{target}.gz" in sums
    (built / "subdir").mkdir()
    assert all(path.is_file() for path in FleetBundle.files(built))

    # Second call hits the cached bundle without invoking pip wheel again.
    assert FleetBundle.from_this() == built
    assert len(wheel_calls) == 2


def test_bundle_from_release_downloads_with_gh_and_caches_tagged_release(
    tmp_path, monkeypatch, capsys
):
    from dreamference.node import fleet_bundle as bundle_module
    from dreamference.node.fleet_bundle import FleetBundle
    from dreamference.runner import codex_branded_builder as builder_module

    monkeypatch.setattr(bundle_module, "BUNDLE_ROOT", str(tmp_path / "bundles"))
    monkeypatch.setattr(
        builder_module.CodexBrandedBuilder, "has_source", classmethod(lambda cls: False)
    )
    monkeypatch.setattr(bundle_module.shutil, "which", lambda name: None)
    assert FleetBundle.build("this") is None
    assert "not installed here" in capsys.readouterr().out

    monkeypatch.setattr(bundle_module.shutil, "which", lambda name: "/usr/bin/gh")
    gh_calls = []

    def fake_gh(argv, **kwargs):
        gh_calls.append(argv)
        if len(gh_calls) == 1:
            return subprocess.CompletedProcess(argv, 1, "", "release not found")
        if argv[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(argv, 0, "v1.8.0\n", "")
        dest = Path(argv[argv.index("-D") + 1])
        (dest / f"ling-{FleetBundle.target()}.sha256sums").write_text("ok\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bundle_module.subprocess, "run", fake_gh)
    assert FleetBundle.build("release=1.7.0") is None
    assert "Could not download the release" in capsys.readouterr().out

    tagged = FleetBundle.build("release=1.7.0")
    assert tagged is not None and FleetBundle.version(tagged) == "v1.7.0"
    assert len(gh_calls) == 2
    assert FleetBundle.build("release=v1.7.0") == tagged
    assert len(gh_calls) == 2

    # The latest release is recorded by its tag: a node installed from it is updated by the next.
    latest = FleetBundle.build("release")
    assert latest is not None and FleetBundle.version(latest) == "v1.8.0"
    assert [call[:3] for call in gh_calls[2:]] == [["gh", "release", "view"], ["gh", "release", "download"]]
    assert "v1.8.0" in gh_calls[3]


def test_bundle_rejects_empty_release_version_and_passes_valid_version(
    tmp_path, monkeypatch, capsys
):
    from dreamference.node.fleet_bundle import FleetBundle

    release_calls = []
    monkeypatch.setattr(
        FleetBundle,
        "from_release",
        classmethod(lambda cls, version: release_calls.append(version) or (tmp_path / "rel")),
    )
    assert FleetBundle.build("release=") is None
    assert "needs a version" in capsys.readouterr().out
    assert release_calls == []

    for empty in ("release=", "release=v"):
        with pytest.raises(ValueError, match="needs a version"):
            FleetBundle.expected_version(empty)

    assert FleetBundle.expected_version("release=1.2.3") == "v1.2.3"
    assert FleetBundle.build("release=1.2.3") == tmp_path / "rel"
    assert release_calls == ["1.2.3"]


def test_a_dry_run_with_empty_release_version_prints_error_and_does_not_crash(fleet, capsys):
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"], dry_run=True, source="release=") == 0
    assert FakeSession.machines["spark-1"].commands == []
    out = capsys.readouterr().out
    assert "needs a version" in out
    assert "nothing, --from release= needs a version" in out


