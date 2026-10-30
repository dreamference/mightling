"""Code search through `ling-code`: when it is asked, what it is asked, and when it cannot answer."""

import json
import subprocess
from pathlib import Path

import pytest

from dreamference.mcp_server import code_index_search
from dreamference.mcp_server.code_index_search import SEARCH_TIMEOUT_S, CodeIndexSearch

ROWS = json.dumps({"rows": [{"path": "a.py", "line": 3, "detail": "function load"}]})


@pytest.fixture
def ling_code(tmp_path, monkeypatch):
    """Installs a stand-in `ling-code` from a shell body; it writes its arguments to `args`."""

    def install(body: str, executable: bool = True) -> Path:
        binary = tmp_path / "install" / "bin" / "ling-code"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\n{body}\n')
        binary.chmod(0o755 if executable else 0o644)
        monkeypatch.setattr(code_index_search, "INSTALL_DIR", str(tmp_path / "install"))
        return tmp_path / "args"

    return install


def test_a_ling_code_that_is_not_executable_is_not_used(ling_code):
    ling_code(f"echo '{ROWS}'", executable=False)
    assert CodeIndexSearch.executable() is None
    assert CodeIndexSearch.search("load") is None


def test_a_blank_query_is_not_sent(ling_code):
    args = ling_code(f"echo '{ROWS}'")
    assert CodeIndexSearch.search("   ") is None
    assert not args.exists()


@pytest.mark.parametrize("top_k, limit", [(0, "1"), (-3, "1"), (7, "7"), (500, "200")])
def test_the_router_is_asked_for_at_least_one_and_at_most_200_rows(ling_code, top_k, limit):
    args = ling_code(f"echo '{ROWS}'")
    CodeIndexSearch.search("load", top_k=top_k)
    assert args.read_text().split() == ["search", "--json", "--limit", limit, "--", "load"]


@pytest.mark.parametrize("code", [1, 3])
def test_a_failed_search_is_no_answer_whatever_it_printed(ling_code, code):
    ling_code(f"echo '{ROWS}'; exit {code}")
    assert CodeIndexSearch.search("load") is None


@pytest.fixture
def recorded_run(ling_code, monkeypatch):
    """Replaces `subprocess.run` with a recorder that answers ROWS; returns the calls' kwargs."""
    ling_code("")
    calls: list = []

    def run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, ROWS, "")

    monkeypatch.setattr(code_index_search.subprocess, "run", run)
    return calls


def test_the_router_runs_in_the_workspace_with_no_input_and_a_time_limit(recorded_run, tmp_path):
    assert CodeIndexSearch.search("load", cwd=str(tmp_path)) == [
        {"rel_path": "a.py", "line": 3, "kind": "function", "symbol": "load", "source": "ling-code"}
    ]
    assert recorded_run == [
        {
            "cwd": str(tmp_path),
            "capture_output": True,
            "text": True,
            "stdin": subprocess.DEVNULL,
            "timeout": SEARCH_TIMEOUT_S,
        }
    ]


def test_a_router_that_runs_out_of_time_is_no_answer(ling_code, monkeypatch):
    ling_code("")

    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(code_index_search.subprocess, "run", run)
    assert CodeIndexSearch.search("load") is None
