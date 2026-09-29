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
