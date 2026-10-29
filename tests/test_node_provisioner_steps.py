"""Tests for provisioning's network choice, OS update and start-and-verify steps.

Every ssh command, HTTP request and sleep is a stand-in.
"""

import io
import json
import subprocess
import urllib.request
from types import SimpleNamespace

import pytest

from dreamference.node import node_provisioner
from dreamference.node.node_pairing import NodePairing
from dreamference.node.node_provisioner import SEARXNG_PORT, NodeProvisioner


@pytest.fixture
def provisioner(tmp_path, monkeypatch):
    """A provisioner for `node-b` that never sleeps."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(node_provisioner.time, "sleep", lambda seconds: None)
    made = NodeProvisioner(["node-b"], {"user": "owner", "model": "org/model"})
    made.results["node-b"] = {
        "host": "node-b",
        "done": [],
        "paired_record": {"node": "n1", "address": "node-b"},
    }
    return made


@pytest.fixture
def web(monkeypatch):
    """Answers HTTP requests from a table of URL to body (bytes) or exception."""
    answers: dict = {}
    asked: list = []

    def urlopen(request, timeout=None):
        url = request.full_url if isinstance(request, urllib.request.Request) else request
        asked.append(url)
        answer = answers.get(url, ConnectionRefusedError(url))
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return SimpleNamespace(answers=answers, asked=asked)


MODELS = "http://node-b:8000/v1/models"
CHAT = "http://node-b:8000/v1/chat/completions"
SEARCH = f"http://node-b:{SEARXNG_PORT}/search?q=ling&format=json"


def test_the_served_model_is_the_first_one_listed(provisioner, web):
    web.answers[MODELS] = json.dumps({"data": [{"id": "org/model"}, {"id": "other"}]}).encode()
    assert provisioner._served_model("node-b", 8000) == "org/model"


@pytest.mark.parametrize("body", [b"<html>", b'{"data": []}', b"{}"])
def test_a_model_server_listing_nothing_serves_nothing(provisioner, web, body):
    web.answers[MODELS] = body
    assert provisioner._served_model("node-b", 8000) is None


def test_a_model_server_that_does_not_answer_serves_nothing(provisioner, web):
    assert provisioner._served_model("node-b", 8000) is None


def test_waiting_for_the_model_gives_up_at_the_start_timeout(provisioner, web, monkeypatch):
    clock = iter([0.0, 0.0, 30.0, 61.0])
    monkeypatch.setattr(node_provisioner.time, "monotonic", lambda: next(clock))
    provisioner.options["start_timeout"] = 60
    assert provisioner._wait_for_model("node-b", 8000) is None
    assert web.asked == [MODELS, MODELS]


def test_a_node_that_answers_a_completion_and_a_search_is_verified(provisioner, web):
    web.answers[CHAT] = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
    web.answers[SEARCH] = b'{"results": []}'
    assert provisioner._verify("node-b", 8000, "org/model") == "ok"


def test_a_node_whose_model_answers_no_choices_is_not_verified(provisioner, web):
    web.answers[CHAT] = b'{"choices": []}'
    assert provisioner._verify("node-b", 8000, "org/model") == "no completion"
    assert web.asked == [CHAT]


def test_a_node_whose_search_does_not_answer_is_not_verified(provisioner, web):
    web.answers[CHAT] = b'{"choices": [1]}'
    assert provisioner._verify("node-b", 8000, "org/model").startswith("failed: ")


@pytest.fixture
def node_runs(monkeypatch):
    """Records each `NodePairing.run` operation and answers `info` with `{}`."""
    operations: list = []

    def run(cls, record, operation, *args, capture=False):
        operations.append(operation)
        return subprocess.CompletedProcess([], 0, "{}", "")

    monkeypatch.setattr(NodePairing, "run", classmethod(run))
    return operations


def test_no_start_leaves_the_node_stopped_and_complete(provisioner, node_runs, web):
    provisioner.options["no_start"] = True
    provisioner._start_and_verify("node-b")
    assert (
        provisioner.results["node-b"]["state"] == "stopped"
        and provisioner.results["node-b"]["complete"]
    )
    assert node_runs == ["info"] and web.asked == []


def test_a_model_already_served_is_verified_without_a_start(provisioner, node_runs, monkeypatch):
    monkeypatch.setattr(NodeProvisioner, "_served_model", lambda self, host, port: "org/model")
    monkeypatch.setattr(NodeProvisioner, "_verify", lambda self, host, port, model: "ok")
    provisioner._start_and_verify("node-b")
    assert node_runs == ["info"]
    assert (
        provisioner.results["node-b"]["state"] == "ready"
        and provisioner.results["node-b"]["complete"]
    )


def test_a_model_that_never_answers_after_the_start_fails_the_start(
    provisioner, node_runs, monkeypatch
):
    monkeypatch.setattr(NodeProvisioner, "_served_model", lambda self, host, port: None)
    monkeypatch.setattr(NodeProvisioner, "_wait_for_model", lambda self, host, port: None)
    provisioner._start_and_verify("node-b")
    assert node_runs == ["info", "start"]
    result = provisioner.results["node-b"]
    assert result["failed"] == "start" and result["left"] == "ling-admin node start node-b"


def test_a_restart_starts_a_served_model_again_and_a_failed_check_is_left_to_status(
    provisioner, node_runs, monkeypatch
):
    provisioner.options["restart"] = True
    monkeypatch.setattr(NodeProvisioner, "_served_model", lambda self, host, port: "org/model")
    monkeypatch.setattr(NodeProvisioner, "_wait_for_model", lambda self, host, port: "org/model")
    monkeypatch.setattr(NodeProvisioner, "_verify", lambda self, host, port, model: "no completion")
    provisioner._start_and_verify("node-b")
    assert node_runs == ["info", "start"]
    result = provisioner.results["node-b"]
    assert result["complete"] is False and result["left"] == "ling-admin node status node-b"


@pytest.mark.parametrize(("code", "stdout"), [(1, '{"model_port": 9000}'), (0, "not json")])
def test_node_info_that_failed_or_is_not_json_is_empty(provisioner, monkeypatch, code, stdout):
    monkeypatch.setattr(
        NodePairing,
        "run",
        classmethod(lambda cls, *a, **k: subprocess.CompletedProcess([], code, stdout, "")),
    )
    assert provisioner._info({"node": "n1"}) == {}


@pytest.fixture
def browse(monkeypatch):
    """Sets what a browse for unprovisioned machines finds."""
    from dreamference.node.node_browser import NodeBrowser

    found: list = []
    monkeypatch.setattr(NodeBrowser, "unprovisioned", classmethod(lambda cls, match=None: found))
    return found


def test_choosing_from_an_empty_network_names_the_command_instead(provisioner, browse, capsys):
    assert provisioner._choose_from_network() is False
    assert "No unprovisioned GB10" in capsys.readouterr().out


def test_choosing_without_a_terminal_lists_the_machines_and_asks_nothing(
    provisioner, browse, monkeypatch, capsys
):
    browse.append({"host": "spark-1a2b.local", "address": "192.0.2.4"})
    monkeypatch.setattr("sys.stdin", io.StringIO())
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked"))
    assert provisioner._choose_from_network() is False
    assert "1. spark-1a2b.local (192.0.2.4)" in capsys.readouterr().out


def test_choosing_keeps_only_numbers_that_name_a_listed_machine(provisioner, browse, monkeypatch):
    browse += [
        {"host": "spark-a", "address": "192.0.2.4"},
        {"host": "spark-b", "address": "192.0.2.5"},
    ]
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt: "2, 0, 3, x, -1, 1")
    assert provisioner._choose_from_network() is True
    assert provisioner.hosts == ["spark-b", "spark-a"]
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert provisioner._choose_from_network() is False


class Session:
    """Stands in for a `FleetSession`, recording what it is asked to run."""

    def __init__(self, update_code: int = 0) -> None:
        self.host = "node-b"
        self.update_code = update_code
        self.ran: list = []

    def sudo(self, line, password):
        """Records a sudo line and answers the update with `update_code`."""
        self.ran.append(line)
        return subprocess.CompletedProcess(
            [], self.update_code if "dist-upgrade" in line else 0, "", ""
        )

    def run_admin(self, args, docker=False):
        """Records an admin command."""
        self.ran.append(" ".join(args))

    def close(self):
        """Records the close."""
        self.ran.append("close")


def test_no_os_update_unless_asked(provisioner):
    session = Session()
    assert provisioner._step_os_update("node-b", session) is True
    assert session.ran == []


def test_a_failed_os_update_does_not_reboot(provisioner):
    provisioner.options["os_update"] = True
    session = Session(update_code=100)
    assert provisioner._step_os_update("node-b", session) is False
    assert "systemctl reboot" not in session.ran
    assert provisioner.results["node-b"]["left"] == "ling-admin node provision node-b --os-update"


def test_an_os_update_stops_a_running_server_and_fails_when_the_machine_does_not_come_back(
    provisioner, monkeypatch
):
    provisioner.options["os_update"] = True
    provisioner.results["node-b"]["probe"] = {"admin": "1"}
    monkeypatch.setattr(NodeProvisioner, "_wait_for_ssh", lambda self, session: False)
    session = Session()
    assert provisioner._step_os_update("node-b", session) is False
    assert session.ran[0] == "server stop" and session.ran[-2:] == ["systemctl reboot", "close"]
    assert (
        provisioner.results["node-b"]["reason"] == "the machine did not come back after the reboot"
    )


def test_an_os_update_probes_the_machine_again_after_the_reboot(provisioner, monkeypatch):
    provisioner.options["os_update"] = True
    monkeypatch.setattr(NodeProvisioner, "_wait_for_ssh", lambda self, session: True)
    monkeypatch.setattr(NodeProvisioner, "_step_probe", lambda self, host, session: "probed")
    assert provisioner._step_os_update("node-b", Session()) == "probed"
    assert "OS and firmware updated" in provisioner.results["node-b"]["done"][-1]
