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
    # reported as a parse error with no id, which left the client waiting forever. Through
    # `_stdio_replies`: from `tmp_path` alone the subprocess imported the editable install, so this
    # test checked the main checkout's server instead of this one.
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
    replies = _stdio_replies(tmp_path, stdin.encode())

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
    with patch("dreamference.mcp_server.web_session.WebSession.get", return_value=response):
        outcome = WebTools.search("python asyncio")
    assert "duckduckgo: HTTP connection error" in outcome["error"]
    # The hint sends the model to a known page and to the user, never to repair the container:
    # the engines refuse upstream, and a restart changes nothing (2026-10-20).
    assert "web_fetch" in outcome["hint"] and "tell the user" in outcome["hint"]
    assert "docker" not in outcome["hint"] and "restart the" not in outcome["hint"]

    response.json.return_value = {"results": [{"title": "t", "url": "u", "content": "c", "engine": "brave"}],
                                  "unresponsive_engines": [["duckduckgo", "timeout"]]}
    with patch("dreamference.mcp_server.web_session.WebSession.get", return_value=response):
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
    assert calls.read_text().strip() == "search --json --limit 2 -- loader load"


def test_code_search_passes_words_that_look_like_options_as_words(tmp_path, monkeypatch):
    # The words came before `--json` with no `--`: a query word such as `--path` was read by
    # `ling-code` as its option (and `-x` failed the call, sending the search to the slow engine).
    from dreamference.mcp_server import CodeIndexSearch

    calls = tmp_path / "calls"
    _fake_mightling_code(tmp_path, monkeypatch, f"echo \"$@\" >> {calls}\necho '{{\"rows\": []}}'\n")
    assert CodeIndexSearch.search("--path vendor -x") == []
    assert calls.read_text().strip() == "search --json --limit 5 -- --path vendor -x"


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


def test_web_search_sends_the_category_time_range_and_detected_language():
    from unittest.mock import MagicMock, patch

    from dreamference.mcp_server.web_tools import WebTools

    response = MagicMock()
    response.json.return_value = {"results": [], "answers": [], "unresponsive_engines": []}
    with patch("dreamference.mcp_server.web_session.WebSession.get", return_value=response) as get:
        WebTools.search("climate summit", categories="social", time_range="week")
        WebTools.search("climate summit", time_range="fortnight")
    first, second = (call.kwargs["params"] for call in get.call_args_list)
    assert first["categories"] == "social media" and first["time_range"] == "week"
    assert first["language"] == "auto"
    assert "time_range" not in second


def _stdio_replies(tmp_path, stdin: bytes, prelude: str = "") -> list:
    """Feeds raw bytes to the real stdio loop of this checkout and returns its replies.

    `prelude` is Python run in the server's process before the loop starts.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    # This checkout first: from `tmp_path` an editable install elsewhere would be imported instead.
    root = str(Path(__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH")]))}
    out = subprocess.run(
        [sys.executable, "-c", prelude + "from dreamference.mcp_server import main; main()"],
        input=stdin, capture_output=True, timeout=60, cwd=tmp_path, env=env,
    ).stdout
    return [json.loads(line) for line in out.decode().splitlines()]


def test_a_line_that_is_not_utf8_is_a_parse_error_and_the_server_keeps_answering(tmp_path):
    ping = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode()
    replies = _stdio_replies(tmp_path, b'{"jsonrpc": "2.0", "id": 1, "method": "\xff\xfe"}\n' + ping + b"\n")
    assert [r.get("id") for r in replies] == [None, 7]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["result"] == {}


def test_a_request_longer_than_64_kib_is_answered_and_the_server_keeps_answering(tmp_path):
    # A diff for ide_apply_diff is easily larger than asyncio's default line limit.
    big = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "ide_apply_diff",
                      "arguments": {"file_path": "a.py", "diff_content": "+x\n" * 50_000}}}
    ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    replies = _stdio_replies(tmp_path, (json.dumps(big) + "\n" + json.dumps(ping) + "\n").encode())
    assert [r.get("id") for r in replies] == [1, 2]
    assert "result" in replies[0]


def test_a_tool_call_whose_params_or_arguments_are_null_is_answered():
    # JSON-RPC allows `null` here; `params.get("arguments", {})` handed None on, and the tool
    # failed with an AttributeError reported as an internal error (-32603).
    for params in ({"name": "ide_get_active_editor", "arguments": None}, None):
        req = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": params}
        resp = MCPServer().handle_request(req)
        assert "result" in resp, resp


def test_code_search_asks_the_router_for_as_many_rows_as_top_k(tmp_path, monkeypatch):
    # Without `--limit` the router answered its default 40 rows, so a `top_k` of 60 got 40; it
    # takes at most 200.
    from dreamference.mcp_server import CodeIndexSearch

    calls = tmp_path / "calls"
    _fake_mightling_code(tmp_path, monkeypatch, f"echo \"$@\" >> {calls}\necho '{{\"rows\": []}}'\n")
    CodeIndexSearch.search("loader", top_k=60)
    CodeIndexSearch.search("loader", top_k=500)
    assert calls.read_text().splitlines() == [
        "search --json --limit 60 -- loader", "search --json --limit 200 -- loader"
    ]


def test_a_line_longer_than_the_limit_is_a_parse_error_and_the_server_keeps_answering(tmp_path):
    # The limit is lowered for the test; the reader drops what it held of the long line, so its
    # tail may come back as a second parse error, but every reply before the ping is one.
    prelude = "import dreamference.mcp_server.mcp_server as m; m.LINE_LIMIT = 4096; "
    ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode()
    replies = _stdio_replies(tmp_path, b'{"x": "' + b"y" * 20_000 + b'"}\n' + ping + b"\n", prelude)
    assert replies[0]["error"] == {"code": -32700, "message": "Parse error: line too long"}
    assert all(r["id"] is None and r["error"]["code"] == -32700 for r in replies[:-1])
    assert replies[-1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_blank_lines_get_no_reply_and_a_request_whose_id_is_zero_gets_one(tmp_path):
    # Zero is an id like any other: only a request with no id at all is a notification.
    ping = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "ping"}).encode()
    replies = _stdio_replies(tmp_path, b"\n   \n" + ping + b"\n\n")
    assert replies == [{"jsonrpc": "2.0", "id": 0, "result": {}}]
