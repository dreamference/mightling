"""Provisioning against what specs/DREAMFERENCE_MIGHTLING_FLEET.md promises, flag by flag.

The stand-in machines and sessions are the ones in test_fleet_provision.py.
"""

import datetime
import subprocess
from pathlib import Path

import test_fleet_provision
from test_fleet_provision import FakeSession, StandIn, provision

from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.node.node_pairing import NodePairing
from dreamference.node.node_provisioner import NodeProvisioner

fleet = test_fleet_provision.fleet


def test_the_provision_flags_default_as_the_spec_says():
    args = DreamferenceCLIController.build_parser().parse_args(["node", "provision"])
    assert args.hosts == [] and args.source == "this" and args.model_source == "copy"
    assert (
        args.user is None
        and args.model is None
        and args.role is None
        and args.start_timeout is None
    )
    assert not (
        args.mesh or args.web or args.no_start or args.no_forge or args.restart or args.dry_run
    )


def test_the_account_is_this_user_unless_named(fleet):
    FakeSession.machines = {"spark-1": StandIn(), "spark-2": StandIn()}
    assert provision(["spark-1"]) == 0
    assert provision(["spark-2"], user="admin") == 0
    users = {record["name"]: record["user"] for record in fleet["paired"].values()}
    assert users == {"spark-1": "owner", "spark-2": "admin"}


def test_via_is_the_address_with_one_host_only(fleet):
    FakeSession.machines = {"spark-1-qsfp": StandIn()}
    assert provision(["spark-1"], via="spark-1-qsfp") == 0
    assert ("open", "spark-1-qsfp") in FakeSession.order
    FakeSession.machines = {"spark-2": StandIn(), "spark-3": StandIn()}
    FakeSession.order = []
    assert provision(["spark-2", "spark-3"], via="spark-1-qsfp") == 0
    assert [entry for entry in FakeSession.order if entry[0] == "open"] == [
        ("open", "spark-2"),
        ("open", "spark-3"),
    ]


def test_mesh_says_it_is_not_built_and_pairs_only_this_machine(fleet, capsys):
    FakeSession.machines = {"spark-1": StandIn(), "spark-2": StandIn()}
    assert provision(["spark-1", "spark-2"], mesh=True) == 0
    assert "--mesh is not built yet" in capsys.readouterr().out
    assert sorted(record["name"] for record in fleet["paired"].values()) == ["spark-1", "spark-2"]


def test_no_forge_reaches_node_enable(fleet):
    FakeSession.machines = {"spark-1": StandIn(), "spark-2": StandIn()}
    assert provision(["spark-1"], no_forge=True) == 0
    assert provision(["spark-2"], no_forge=True, web=True) == 0
    enables = [
        c
        for host in ("spark-1", "spark-2")
        for c in FakeSession.machines[host].commands
        if "node enable" in c
    ]
    assert enables == [
        "ling-admin node enable --no-web --no-forge",
        "ling-admin node enable --no-forge",
    ]


def test_a_machine_refusing_the_shared_password_is_asked_once_then_left(fleet, monkeypatch):
    FakeSession.machines = {"spark-1": StandIn(), "spark-2": StandIn()}

    def refusing_open(self):
        FakeSession.order.append(("open", self.host))
        return self.host != "spark-1"

    monkeypatch.setattr(FakeSession, "open", refusing_open)
    provisioner = NodeProvisioner(["spark-1", "spark-2"], {})
    assert provisioner.run() == 1
    assert fleet["asked"] == [
        "Password for owner on all 2 machines: ",
        "spark-1 refused that password. Its own password for owner: ",
    ]
    assert FakeSession.order.count(("open", "spark-1")) == 2
    assert provisioner.results["spark-1"]["failed"] == "connect"
    assert provisioner.results["spark-2"]["complete"] is True


def test_the_node_record_says_what_was_provisioned(fleet, monkeypatch):
    saved = []
    monkeypatch.setattr(NodePairing, "_save", classmethod(lambda cls, record: saved.append(record)))
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"]) == 0
    assert saved[-1]["provisioned"] == {
        "bundle": "this-test",
        "model": "qwen3.8-27b-nvfp4-dflash2",
        "image": "lmsysorg/sglang@sha256:x",
        "date": datetime.date.today().isoformat(),
    }


def test_each_machine_logs_to_its_own_dated_file(fleet, monkeypatch):
    from dreamference.node import node_provisioner

    logs = {}

    class LoggingSession(FakeSession):
        def __init__(self, host, user, run_dir, ssh_port=22, log_path=None):
            super().__init__(host, user, run_dir, ssh_port, log_path)
            logs[host] = log_path

    monkeypatch.setattr(node_provisioner, "FleetSession", LoggingSession)
    FakeSession.machines = {"spark-1": StandIn()}
    assert provision(["spark-1"]) == 0
    today = datetime.date.today().isoformat()
    assert logs == {"spark-1": Path(node_provisioner.LOG_DIR) / f"{today}-spark-1.log"}
    assert node_provisioner.LOG_DIR.endswith("/.local/state/dreamference/fleet")


def test_a_failed_advert_leaves_the_enable_it_tried(fleet, monkeypatch):
    run = FakeSession.run

    def refusing_enable(self, script, **options):
        if "node enable" in script:
            return subprocess.CompletedProcess(script, 1, "no avahi", "")
        return run(self, script, **options)

    monkeypatch.setattr(FakeSession, "run", refusing_enable)
    FakeSession.machines = {"spark-1": StandIn()}
    provisioner = NodeProvisioner(["spark-1"], {"web": True, "no_forge": True})
    assert provisioner.run() == 1
    result = provisioner.results["spark-1"]
    assert result["failed"] == "advertise"
    assert result["left"] == "ssh owner@spark-1 ling-admin node enable --no-forge"


def test_a_running_server_given_a_new_model_or_image_waits_for_restart(fleet, monkeypatch):
    requests = []
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, record, request, capture=True, input_text=None: (
                requests.append(request)
                or subprocess.CompletedProcess([], 0, '{"model_port": 8000}', "")
            )
        ),
    )
    FakeSession.machines = {"spark-1": StandIn()}
    provisioner = NodeProvisioner(["spark-1"], {})
    assert provisioner.run() == 0
    result = provisioner.results["spark-1"]
    assert result["state"] == "restart pending" and "start" not in requests
    assert result["left"] == "ling-admin node provision spark-1 --restart"


def test_restart_starts_the_changed_server_again(fleet, monkeypatch):
    requests = []
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(
            lambda cls, record, request, capture=True, input_text=None: (
                requests.append(request)
                or subprocess.CompletedProcess([], 0, '{"model_port": 8000}', "")
            )
        ),
    )
    monkeypatch.setattr(NodeProvisioner, "_wait_for_model", lambda self, host, port: "qwen")
    FakeSession.machines = {"spark-1": StandIn()}
    provisioner = NodeProvisioner(["spark-1"], {"restart": True})
    assert provisioner.run() == 0
    assert provisioner.results["spark-1"]["state"] == "ready" and "start" in requests
    assert "left" not in provisioner.results["spark-1"]


def test_a_running_server_with_nothing_new_is_ready(fleet):
    machine = StandIn(ready=True)
    machine.state["model"] = "qwen3.8-27b-nvfp4-dflash2"
    machine.state["venv"] = "1"
    FakeSession.machines = {"spark-1": machine}
    provisioner = NodeProvisioner(["spark-1"], {})
    assert provisioner.run() == 0
    assert (
        provisioner.results["spark-1"]["state"] == "ready"
        and "left" not in provisioner.results["spark-1"]
    )
