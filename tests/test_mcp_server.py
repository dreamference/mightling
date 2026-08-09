import json
from neurr.mcp_server import MCPServer

def test_mcp_initialize():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    resp = server.handle_request(req)
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "neurr-mcp-server"

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
