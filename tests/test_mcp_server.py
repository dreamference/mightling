import json
from dreamference.mcp_server import MCPServer

def test_mcp_initialize():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    resp = server.handle_request(req)
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "dreamference-mcp-server"

def test_mcp_list_tools():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = server.handle_request(req)
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "ide_get_active_editor" in tool_names
    assert "ide_get_diagnostics" in tool_names
    assert "workspace_search_code" in tool_names

def test_mcp_call_tool():
    server = MCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "ide_open_file",
            "arguments": {"file_path": "main.py", "line": 10, "column": 5}
        }
    }
    resp = server.handle_request(req)
    assert "result" in resp
    content = json.loads(resp["result"]["content"][0]["text"])
    assert content["status"] == "success"


def test_stdio_loop_follows_json_rpc(tmp_path):
    # Drives the real loop. Notifications used to be answered with -32601, and a failing tool was
    # reported as a parse error with no id, which left the client waiting forever.
    import json
    import subprocess
    import sys

    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "workspace_search_code", "arguments": {"query": "x", "top_k": "many"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "ide_apply_diff", "arguments": {"file_path": "a.py", "diff_content": "-x\n+y"}}},
    ]
    stdin = "\n".join(json.dumps(line) for line in lines) + "\n{not json\n"
    out = subprocess.run(
        [sys.executable, "-c", "from dreamference.mcp_server import main; main()"],
        input=stdin, capture_output=True, text=True, timeout=60, cwd=tmp_path,
    ).stdout
    replies = [json.loads(line) for line in out.splitlines()]

    assert [r.get("id") for r in replies] == [1, 2, 3, 4, None]  # nothing for the notification
    assert replies[1]["result"] == {}
    assert replies[2]["error"]["code"] == -32603
    assert json.loads(replies[3]["result"]["content"][0]["text"])["status"] == "not_applied"
    assert replies[4]["error"]["code"] == -32700


def test_web_search_reports_engines_that_all_failed_rather_than_no_results():
    # A SearXNG container with stale DNS answered every query with zero results and a list of
    # unresponsive engines; the tool passed that on as "0 results".
    from unittest.mock import MagicMock, patch
    from dreamference.mcp_server.web_tools import WebTools

    response = MagicMock()
    response.json.return_value = {
        "results": [], "answers": [],
        "unresponsive_engines": [["duckduckgo", "HTTP connection error"], ["brave", "HTTP connection error"]],
    }
    with patch("dreamference.mcp_server.web_tools.requests.get", return_value=response):
        outcome = WebTools.search("python asyncio")
    assert "duckduckgo: HTTP connection error" in outcome["error"]
    assert "docker restart dreamference-searxng" in outcome["hint"]

    response.json.return_value = {"results": [{"title": "t", "url": "u", "content": "c", "engine": "brave"}],
                                  "unresponsive_engines": [["duckduckgo", "timeout"]]}
    with patch("dreamference.mcp_server.web_tools.requests.get", return_value=response):
        outcome = WebTools.search("python asyncio")
    assert outcome["result_count"] == 1 and "error" not in outcome


def _fake_mightling_code(tmp_path, monkeypatch, script: str):
    """Installs a stand-in `ling-code` where CodeIndexSearch looks for the real one."""
    from dreamference.mcp_server import code_index_search

    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    binary = install / "bin" / "ling-code"
    binary.write_text("#!/bin/sh\n" + script)
    binary.chmod(0o755)
    monkeypatch.setattr(code_index_search, "INSTALL_DIR", str(install))
    return binary


def test_code_search_is_answered_by_the_code_index_when_the_workspace_has_one(tmp_path, monkeypatch):
    # The router's rows, not the context engine: no workspace walk and no embedding model.
    answer = {"op": "search", "rows": [
        {"tag": None, "path": "pkg/a.py", "line": 15, "detail": "class pkg.a.Loader"},
        {"tag": None, "path": "pkg/a.py", "line": 40, "detail": "method pkg.a.Loader.load"},
        {"tag": None, "path": "pkg/b.py", "line": 3, "detail": "function pkg.b.helper"},
    ]}
    calls = tmp_path / "calls"
    _fake_mightling_code(tmp_path, monkeypatch, f"echo \"$@\" >> {calls}\ncat <<'JSON'\n{json.dumps(answer)}\nJSON\n")
    server = MCPServer()
    monkeypatch.setattr(MCPServer, "context_engine", property(lambda self: (_ for _ in ()).throw(AssertionError("engine built"))))
    result = server.execute_tool("workspace_search_code", {"query": "loader load", "top_k": 2})
    assert result == [
        {"rel_path": "pkg/a.py", "line": 15, "kind": "class", "symbol": "pkg.a.Loader", "source": "ling-code"},
        {"rel_path": "pkg/a.py", "line": 40, "kind": "method", "symbol": "pkg.a.Loader.load", "source": "ling-code"},
    ]
    # The words are separate arguments: no shell, and nothing the query says is interpreted.
    assert calls.read_text().strip() == "search loader load --json"


def test_code_search_falls_back_to_the_context_engine(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from dreamference.mcp_server import CodeIndexSearch

    engine = MagicMock()
    engine.search_code.return_value = [{"rel_path": "x.py", "score": 1.0}]
    monkeypatch.setattr(MCPServer, "context_engine", property(lambda self: engine))
    # Not installed.
    monkeypatch.setattr("dreamference.mcp_server.code_index_search.INSTALL_DIR", str(tmp_path / "nowhere"))
    assert CodeIndexSearch.executable() is None
    assert MCPServer().execute_tool("workspace_search_code", {"query": "x"}) == [{"rel_path": "x.py", "score": 1.0}]
    # Installed, but this workspace has no index (exit 3), or the call fails, or prints no JSON.
    for script in ("echo 'no code index for this repository yet'\nexit 3\n", "exit 1\n", "echo not json\n"):
        _fake_mightling_code(tmp_path / script[:6].strip().replace(" ", "_"), monkeypatch, script)
        assert CodeIndexSearch.search("x") is None
        assert MCPServer().execute_tool("workspace_search_code", {"query": "x"}) == [{"rel_path": "x.py", "score": 1.0}]
    assert engine.index_workspace.call_count == 4
