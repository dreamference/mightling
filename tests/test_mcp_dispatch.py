"""The MCP server's dispatch: JSON-RPC answers, the IDE tools, and what each tool is handed."""

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

from dreamference.mcp_server import CodeIndexSearch, MCPServer
from dreamference.mcp_server import mcp_server as server_module
from dreamference.mcp_server.ide_state import IDEState
from dreamference.mcp_server.mcp_tool_registry import MCPToolRegistry
from dreamference.mcp_server.web_tools import TIME_RANGES, WebTools


def call(name: str, arguments: dict | None) -> object:
    """Calls tool `name` through `tools/call` and returns what its text content holds."""
    request = {"jsonrpc": "2.0", "id": 5, "method": "tools/call"}
    request["params"] = {"name": name, "arguments": arguments}
    reply = MCPServer().handle_request(request)
    return json.loads(reply["result"]["content"][0]["text"])


@pytest.fixture
def ide(monkeypatch) -> IDEState:
    """A fresh IDE state for the server, so no test sees another's open files."""
    state = IDEState()
    monkeypatch.setattr(server_module, "global_ide_state", state)
    return state


def test_an_unknown_method_is_a_method_not_found_error_with_the_request_id():
    reply = MCPServer().handle_request({"jsonrpc": "2.0", "id": 41, "method": "resources/list"})
    assert reply["id"] == 41
    assert reply["error"]["code"] == -32601
    assert "resources/list" in reply["error"]["message"]


def test_an_unknown_tool_answers_that_it_is_not_implemented():
    assert call("ide_format_file", {}) == {"error": "Tool 'ide_format_file' not implemented"}


def test_a_tool_that_reads_its_arguments_is_answered_when_they_are_null(ide):
    ide.diagnostics = [{"file": "a.py", "message": "unused"}]
    assert call("ide_get_diagnostics", None) == ide.diagnostics


def test_the_active_editor_is_none_until_a_file_is_opened_then_where_it_was_opened(ide):
    assert call("ide_get_active_editor", {}) == {
        "active_file": "No active file",
        "line": 1,
        "column": 1,
        "selection": "",
    }
    assert call("ide_open_file", {"file_path": "main.py", "line": 10, "column": 5}) == {
        "status": "success",
        "message": "Opened main.py at line 10:5",
    }
    assert call("ide_get_active_editor", {}) == {
        "active_file": "main.py",
        "line": 10,
        "column": 5,
        "selection": "",
    }
    call("ide_open_file", {"file_path": "util.py"})
    assert call("ide_get_active_editor", {})["line"] == 1
    assert call("ide_get_active_editor", {})["column"] == 1
    assert call("ide_get_open_files", {}) == {"open_files": ["main.py", "util.py"]}


def test_diagnostics_are_those_of_the_file_asked_for_or_all_of_them(ide):
    ide.diagnostics = [{"file": "a.py", "message": "one"}, {"file": "b.py", "message": "two"}]
    assert call("ide_get_diagnostics", {"file_path": "a.py"}) == [ide.diagnostics[0]]
    assert call("ide_get_diagnostics", {}) == ide.diagnostics


def test_an_editor_reopened_is_listed_once_and_keeps_its_selection():
    state = IDEState()
    state.set_active_editor("a.py", 3, 4, selection="picked")
    state.set_active_editor("a.py", 7, 1)
    state.set_active_editor("b.py", 2, 2, selection="other")
    assert state.open_files == ["a.py", "b.py"]
    assert (state.active_file, state.cursor_line, state.cursor_column) == ("b.py", 2, 2)
    assert state.selection_text == "other"


def test_web_search_hands_the_tool_its_arguments_or_the_defaults(monkeypatch):
    search = MagicMock(return_value={"results": []})
    monkeypatch.setattr(WebTools, "search", search)
    call("web_search", {"query": "lisbon"})
    call(
        "web_search",
        {"query": "news", "max_results": 3, "category": "news", "time_range": "week"},
    )
    call("web_search", {"query": "x", "category": None, "time_range": None})
    assert [c.args for c in search.call_args_list] == [
        ("lisbon", 8, "general", "auto", ""),
        ("news", 3, "news", "auto", "week"),
        ("x", 8, "general", "auto", ""),
    ]


def test_web_fetch_hands_the_tool_its_url_and_length_or_the_default(monkeypatch):
    fetch = MagicMock(return_value={"text": ""})
    monkeypatch.setattr(WebTools, "fetch", fetch)
    call("web_fetch", {"url": "https://example.org/a"})
    call("web_fetch", {"url": "https://example.org/b", "max_chars": 300})
    assert [c.args for c in fetch.call_args_list] == [
        ("https://example.org/a", 20000),
        ("https://example.org/b", 300),
    ]


def test_code_search_asks_the_index_for_five_rows_unless_told(monkeypatch):
    search = MagicMock(return_value=[])
    monkeypatch.setattr(CodeIndexSearch, "search", search)
    call("workspace_search_code", {"query": "loader"})
    call("workspace_search_code", {"query": "loader", "top_k": 9})
    assert [(c.args, c.kwargs) for c in search.call_args_list] == [
        (("loader",), {"top_k": 5}),
        (("loader",), {"top_k": 9}),
    ]


def test_an_index_that_finds_nothing_is_the_answer_and_no_engine_is_built(monkeypatch):
    # An empty list is the router's answer; only None (it cannot answer here) falls back.
    monkeypatch.setattr(CodeIndexSearch, "search", MagicMock(return_value=[]))
    monkeypatch.setattr(
        MCPServer,
        "context_engine",
        property(lambda self: (_ for _ in ()).throw(AssertionError("engine built"))),
    )
    assert call("workspace_search_code", {"query": "nothing matches"}) == []


def test_the_context_engine_is_built_once_and_only_when_first_asked_for(monkeypatch):
    built = MagicMock(side_effect=lambda: object())
    module = types.ModuleType("dreamference.context_engine")
    module.ContextEngine = built  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dreamference.context_engine", module)
    server = MCPServer()
    assert built.call_count == 0
    engine = server.context_engine
    assert server.context_engine is engine
    assert built.call_count == 1


def test_the_tool_list_is_what_the_server_dispatches_with_its_required_arguments():
    tools = {tool["name"]: tool["inputSchema"] for tool in MCPToolRegistry.get_tool_definitions()}
    assert {name: schema.get("required", []) for name, schema in tools.items()} == {
        "ide_get_active_editor": [],
        "ide_get_diagnostics": [],
        "ide_get_open_files": [],
        "ide_open_file": ["file_path"],
        "ide_apply_diff": ["file_path", "diff_content"],
        "web_search": ["query"],
        "web_fetch": ["url"],
        "workspace_search_code": ["query"],
    }
    # The time ranges offered are the ones the search passes on to SearXNG.
    assert set(tools["web_search"]["properties"]["time_range"]["enum"]) == TIME_RANGES
