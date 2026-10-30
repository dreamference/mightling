"""`ling-admin searxng {start,status}`: its exit codes, and the node told only of a running SearXNG."""

import pytest

from dreamference.chat.searxng_sidecar import SearxngSidecar
from dreamference.chat.searxng_status import SearxngStatus
from dreamference.cli.searxng_command import SEARXNG_URL, SearxngCommand
from dreamference.node.node_advertiser import NodeAdvertiser


@pytest.mark.parametrize(("started", "code", "told"), [(False, 1, []), (True, 0, ["told"])])
def test_start_tells_the_node_only_once_searxng_runs(monkeypatch, started, code, told):
    told_clients = []
    monkeypatch.setattr(SearxngSidecar, "start", classmethod(lambda cls, say=None: started))
    monkeypatch.setattr(
        NodeAdvertiser, "on_searxng_started", classmethod(lambda cls: told_clients.append("told"))
    )
    assert SearxngCommand.run("start") == code
    assert told_clients == told


@pytest.mark.parametrize("code", [0, 1, 2])
def test_status_exits_with_the_state_of_the_engines(monkeypatch, capsys, code):
    asked = []

    def report(cls, container, base_url):
        asked.append(base_url)
        return [f"engine state {code}"], code

    monkeypatch.setattr(SearxngStatus, "report_lines", classmethod(report))
    assert SearxngCommand.run("status") == code
    assert asked == [SEARXNG_URL]
    assert f"engine state {code}" in capsys.readouterr().out


@pytest.mark.parametrize("subcommand", [None, "stop"])
def test_anything_else_prints_the_usage_and_starts_nothing(monkeypatch, subcommand):
    monkeypatch.setattr(
        SearxngSidecar, "start", classmethod(lambda cls, say=None: pytest.fail("nothing starts"))
    )
    assert SearxngCommand.run(subcommand) == 2
