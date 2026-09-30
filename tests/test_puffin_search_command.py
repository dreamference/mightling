"""`puffin-search` is its own console script, and `puffin-admin search` is gone."""

import json
from unittest.mock import patch

import pytest

from dreamference.cli import DreamferenceCLIController, PuffinSearchCommand

RESULTS = {
    "answers": ["42"],
    "results": [{"title": "Lisbon weather", "url": "https://example.com/lisbon", "snippet": "Sunny, 24C"}],
}


def test_search_prints_answers_and_numbered_results(capsys):
    with patch("dreamference.mcp_server.web_tools.WebTools.search", return_value=RESULTS) as search:
        assert PuffinSearchCommand.main(["lisbon", "weather", "-n", "3"]) == 0
    search.assert_called_once_with("lisbon weather", max_results=3)
    out = capsys.readouterr().out
    assert "ANSWER: 42" in out
    assert "1. Lisbon weather\n   https://example.com/lisbon\n   Sunny, 24C" in out


def test_search_emits_json_on_request(capsys):
    with patch("dreamference.mcp_server.web_tools.WebTools.search", return_value=RESULTS):
        assert PuffinSearchCommand.main(["--json", "q"]) == 0
    assert json.loads(capsys.readouterr().out) == RESULTS


def test_search_reports_an_unreachable_instance_with_its_hint(capsys):
    failure = {"error": "SearXNG unreachable", "hint": "puffin-admin puffin start"}
    with patch("dreamference.mcp_server.web_tools.WebTools.search", return_value=failure):
        assert PuffinSearchCommand.main(["q"]) == 1
    out = capsys.readouterr().out
    assert "❌ SearXNG unreachable" in out and "💡 puffin-admin puffin start" in out


def test_puffin_admin_no_longer_has_a_search_subcommand():
    with pytest.raises(SystemExit):
        DreamferenceCLIController.build_parser().parse_args(["search", "q"])
