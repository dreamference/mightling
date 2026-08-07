import sys
import json
import os
from typing import Dict, Any, List, Optional
from dgxcoder.context_engine import ContextEngine

class IDEState:
    """Mock/Bridge state store for connected IDE editor sessions."""
    def __init__(self):
        self.active_file: Optional[str] = None
        self.cursor_line: int = 1
        self.cursor_column: int = 1
        self.selection_text: str = ""
        self.open_files: List[str] = []
        self.diagnostics: List[Dict[str, Any]] = []

    def set_active_editor(self, file_path: str, line: int = 1, col: int = 1, selection: str = ""):
        self.active_file = file_path
        self.cursor_line = line
        self.cursor_column = col
        self.selection_text = selection
        if file_path not in self.open_files:
            self.open_files.append(file_path)

global_ide_state = IDEState()

class MCPServer:
    """Model Context Protocol (MCP) Stdio Server for JetBrains & VS Code Integration."""

    def __init__(self):
        self.context_engine = ContextEngine()

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        method = request.get("method")
        msg_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dgxcoder-mcp-server", "version": "1.2.0"}
                }
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": "ide_get_active_editor",
                            "description": "Returns current active open editor file path, selection, and cursor position.",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "ide_get_diagnostics",
                            "description": "Returns active linter diagnostics and syntax errors in the workspace.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"file_path": {"type": "string", "description": "Optional target file path"}}
                            }
                        },
                        {
                            "name": "ide_get_open_files",
                            "description": "Lists all files currently open in JetBrains or VS Code tabs.",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "ide_open_file",
                            "description": "Opens a file in the IDE editor at specified line and column.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "line": {"type": "integer"},
                                    "column": {"type": "integer"}
                                },
                                "required": ["file_path"]
                            }
                        },
                        {
                            "name": "ide_apply_diff",
                            "description": "Applies visual code diff to target file with inline red/green preview.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "file_path": {"type": "string"},
                                    "diff_content": {"type": "string"}
                                },
                                "required": ["file_path", "diff_content"]
                            }
                        },
                        {
                            "name": "workspace_search_code",
                            "description": "Performs local AST & vector semantic code search in the workspace.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string"},
                                    "top_k": {"type": "integer"}
                                },
                                "required": ["query"]
                            }
                        }
                    ]
                }
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            result_content = self.execute_tool(tool_name, args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result_content, indent=2)}]
                }
            }

        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method '{method}' not found"}
            }

    def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if tool_name == "ide_get_active_editor":
            return {
                "active_file": global_ide_state.active_file or "No active file",
                "line": global_ide_state.cursor_line,
                "column": global_ide_state.cursor_column,
                "selection": global_ide_state.selection_text
            }

        elif tool_name == "ide_get_diagnostics":
            target_file = args.get("file_path")
            if target_file:
                return [d for d in global_ide_state.diagnostics if d.get("file") == target_file]
            return global_ide_state.diagnostics

        elif tool_name == "ide_get_open_files":
            return {"open_files": global_ide_state.open_files}

        elif tool_name == "ide_open_file":
            path = args.get("file_path", "")
            line = args.get("line", 1)
            col = args.get("column", 1)
            global_ide_state.set_active_editor(path, line, col)
            return {"status": "success", "message": f"Opened {path} at line {line}:{col}"}

        elif tool_name == "ide_apply_diff":
            path = args.get("file_path", "")
            diff = args.get("diff_content", "")
            return {"status": "success", "message": f"Applied diff overlay to {path}", "diff_applied": diff}

        elif tool_name == "workspace_search_code":
            query = args.get("query", "")
            top_k = args.get("top_k", 5)
            self.context_engine.index_workspace()
            return self.context_engine.search_code(query, top_k=top_k)

        return {"error": f"Tool '{tool_name}' not implemented"}

    def run_stdio(self):
        """Runs the JSON-RPC stdio loop for Goose / MCP integration."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = self.handle_request(req)
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            except Exception as e:
                err_resp = {"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {str(e)}"}}
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()

def main():
    server = MCPServer()
    server.run_stdio()

if __name__ == "__main__":
    main()
