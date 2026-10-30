"""The correctness canary of `ling-admin benchmark` and the dataset `benchmark_server` stages.

The canary judges whether a served model still answers correctly (arithmetic, code that parses,
a real tool call), since a wrong kernel on SM12x emits garbage rather than raising. The sonnet
dataset is written to the host and copied into the running container; neither test reaches
Docker or a server.
"""

import subprocess

import pytest

from dreamference.cli import dreamference_cli_controller as controller
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.cli.sonnet_dataset import SONNET_TEXT


class Reply:
    def __init__(self, status_code, message=None):
        self.status_code = status_code
        self.message = message

    def json(self):
        return {"choices": [{"message": self.message}]}


class Session:
    """Answers the three probes in order: arithmetic, code, tool call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.payloads = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def canary(*replies):
    session = Session(*replies)
    rows = DreamferenceCLIController._run_correctness_canary(
        session, "http://localhost:8000/v1/chat/completions", "served/model", {}
    )
    return session, rows


TOOL_CALL = {
    "tool_calls": [{"function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}]
}


def test_a_healthy_model_passes_all_three_probes():
    session, rows = canary(
        Reply(200, {"content": "391"}),
        Reply(200, {"content": "```python\ndef add(a, b):\n    return a + b\n```"}),
        Reply(200, TOOL_CALL),
    )
    assert all(row.startswith("[green]pass") for row in rows.values()), rows
    assert "get_weather" in rows["Canary: tool call"]
    # Thinking off for every probe, and only the last one offers a tool.
    assert [p["chat_template_kwargs"] for p in session.payloads] == [{"enable_thinking": False}] * 3
    assert ["tools" in p for p in session.payloads] == [False, False, True]
    assert session.payloads[2]["tool_choice"] == "auto"


def test_garbage_fails_each_probe_for_its_own_reason():
    _, rows = canary(
        Reply(200, {"content": "!!!!!!!!"}),
        Reply(200, {"content": "def add(a, b) return a +"}),
        Reply(200, {"content": "I would call get_weather for Paris."}),
    )
    assert rows["Canary: arithmetic"].startswith("[red]FAIL[/red] — expected 391, got '!!!!!!!!'")
    assert rows["Canary: code"].startswith("[red]FAIL[/red] — output does not parse")
    assert "check --tool-call-parser" in rows["Canary: tool call"]


def test_a_missing_content_is_a_failure_not_a_crash():
    _, rows = canary(Reply(200, {"content": None}), Reply(200, {"content": None}), Reply(200, {}))
    assert rows["Canary: arithmetic"].startswith("[red]FAIL")
    assert rows["Canary: tool call"].startswith("[red]FAIL")


@pytest.mark.parametrize("failure", [Reply(503), OSError("refused")], ids=["http-error", "raises"])
def test_an_unreachable_server_is_reported_unreachable_not_failed(failure):
    _, rows = canary(failure, failure, failure)
    assert set(rows.values()) == {"[red]unreachable[/red]"}


def test_the_sonnet_dataset_is_written_and_copied_into_the_container(monkeypatch, tmp_path):
    host = tmp_path / "sonnet.txt"
    runs = []
    monkeypatch.setattr(controller, "SONNET_HOST_PATH", str(host))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: runs.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    path = DreamferenceCLIController._provision_sonnet_dataset("dreamference-vllm-8000")
    assert path == "/tmp/sonnet.txt"
    assert host.read_text(encoding="utf-8") == SONNET_TEXT
    assert runs == [["docker", "cp", str(host), "dreamference-vllm-8000:/tmp/sonnet.txt"]]


def test_a_dataset_that_cannot_be_staged_is_never_copied(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(controller, "SONNET_HOST_PATH", str(tmp_path / "missing" / "sonnet.txt"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("docker cp ran"))
    assert DreamferenceCLIController._provision_sonnet_dataset("dreamference-vllm-8000") is None
    assert "Could not stage the sonnet dataset" in capsys.readouterr().out


def test_benchmark_server_stops_when_the_copy_fails_and_never_runs_the_benchmark(
    monkeypatch, tmp_path, capsys
):
    runs = []

    def run(cmd, **k):
        runs.append(cmd[:2])
        return subprocess.CompletedProcess(cmd, 1, "", "Error: No such container")

    monkeypatch.setattr(controller, "SONNET_HOST_PATH", str(tmp_path / "sonnet.txt"))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["benchmark_server"])
    assert exit_info.value.code == 1
    assert runs == [["docker", "cp"]]
    assert "No such container" in capsys.readouterr().out


def test_benchmark_server_runs_the_benchmark_on_the_copied_dataset(monkeypatch, tmp_path):
    runs = []

    def run(cmd, **k):
        runs.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(controller, "SONNET_HOST_PATH", str(tmp_path / "sonnet.txt"))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    DreamferenceCLIController.run_cli(["benchmark_server", "--port", "8100"])
    bench = runs[-1]
    assert bench[:4] == ["docker", "exec", "-i", "dreamference-vllm-8100"]
    assert bench[bench.index("--dataset-path") + 1] == "/tmp/sonnet.txt"
    assert bench[bench.index("--base-url") + 1] == "http://localhost:8100"
