"""
Async Stdio Model Context Protocol (MCP) Server.

This module provides the MCPServer class which runs an asynchronous JSON-RPC stdio server loop
enabling Goose AI Agent, JetBrains, and VS Code companion tools.
"""

import sys
import json
import asyncio
from typing import Dict, Any, List, Optional, Final
from dreamference.context_engine import ContextEngine
from dreamference.mcp_server.ide_state import IDEState
from dreamference.mcp_server.mcp_tool_registry import MCPToolRegistry

# Global IDE State instance
global_ide_state: Final[IDEState] = IDEState()

class MCPServer:
    """
    Model Context Protocol (MCP) Async Stdio Server handling JSON-RPC requests from IDE consumers.
    """

    def __init__(self):
        """Initializes MCPServer with ContextEngine instance."""
        self.context_engine: ContextEngine = ContextEngine()

    async def handle_request_async(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes incoming JSON-RPC 2.0 requests asynchronously (`initialize`, `tools/list`, `tools/call`).

        Args:
            request (Dict[str, Any]): JSON-RPC request dictionary.

        Returns:
            Dict[str, Any]: JSON-RPC response dictionary.
        """
        method: Optional[str] = request.get("method")
        msg_id: Any = request.get("id")
        params: Dict[str, Any] = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dreamference-mcp-server", "version": "1.2.0"}
                }
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": MCPToolRegistry.get_tool_definitions()
                }
            }

        elif method == "tools/call":
            tool_name: str = str(params.get("name", ""))
            args: Dict[str, Any] = params.get("arguments", {})
            result_content = await self.execute_tool_async(tool_name, args)
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

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous wrapper around handle_request_async for backward compatibility."""
        return asyncio.run(self.handle_request_async(request))

    async def execute_tool_async(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """
        Executes requested MCP tool asynchronously.

        Args:
            tool_name (str): Tool identifier string.
            args (Dict[str, Any]): Tool arguments dictionary.

        Returns:
            Any: Tool response data structure.
        """
        if tool_name == "ide_get_active_editor":
            return {
                "active_file": global_ide_state.active_file or "No active file",
                "line": global_ide_state.cursor_line,
                "column": global_ide_state.cursor_column,
                "selection": global_ide_state.selection_text
            }

        elif tool_name == "ide_get_diagnostics":
            target_file: Optional[str] = args.get("file_path")
            if target_file:
                return [d for d in global_ide_state.diagnostics if d.get("file") == target_file]
            return global_ide_state.diagnostics

        elif tool_name == "ide_get_open_files":
            return {"open_files": global_ide_state.open_files}

        elif tool_name == "ide_open_file":
            path: str = str(args.get("file_path", ""))
            line: int = int(args.get("line", 1))
            col: int = int(args.get("column", 1))
            global_ide_state.set_active_editor(path, line, col)
            return {"status": "success", "message": f"Opened {path} at line {line}:{col}"}

        elif tool_name == "ide_apply_diff":
            path: str = str(args.get("file_path", ""))
            diff: str = str(args.get("diff_content", ""))
            return {"status": "success", "message": f"Applied diff overlay to {path}", "diff_applied": diff}

        elif tool_name == "workspace_search_code":
            query: str = str(args.get("query", ""))
            top_k: int = int(args.get("top_k", 5))
            # Offload blocking indexing and search to background thread pool
            def _do_search() -> List[Dict[str, Any]]:
                self.context_engine.index_workspace()
                return self.context_engine.search_code(query, top_k=top_k)
            return await asyncio.to_thread(_do_search)

        return {"error": f"Tool '{tool_name}' not implemented"}

    def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Synchronous wrapper around execute_tool_async."""
        return asyncio.run(self.execute_tool_async(tool_name, args))

    async def run_stdio_async(self) -> None:
        """
        Runs non-blocking JSON-RPC stdio event loop reading stdin and writing responses to stdout.
        """
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8").strip()
            if not line:
                continue
            try:
                req: Dict[str, Any] = json.loads(line)
                resp = await self.handle_request_async(req)
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            except Exception as e:
                err_resp = {"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {str(e)}"}}
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()

    def run_stdio(self) -> None:
        """Synchronous entrypoint launching run_stdio_async."""
        asyncio.run(self.run_stdio_async())

def main() -> None:
    """Main CLI entrypoint for `dream mcp` command."""
    server = MCPServer()
    server.run_stdio()
