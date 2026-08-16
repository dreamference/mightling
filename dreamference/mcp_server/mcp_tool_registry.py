"""
Model Context Protocol (MCP) Tool Schema Registry.

This module provides the MCPToolRegistry class which defines available tools for IDE
integration (`ide_get_active_editor`, `ide_get_diagnostics`, `ide_get_open_files`,
`ide_open_file`, `ide_apply_diff`, `workspace_search_code`).
"""

from typing import Dict, Any, List

class MCPToolRegistry:
    """
    Registry providing JSON-RPC tool schema definitions for MCP consumers.
    """

    @classmethod
    def get_tool_definitions(cls) -> List[Dict[str, Any]]:
        """
        Returns complete tool definition schemas array following Model Context Protocol spec.

        Returns:
            List[Dict[str, Any]]: List of tool schema objects.
        """
        return [
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
                "name": "web_search",
                "description": (
                    "Searches the public web and returns titles, URLs and snippets. Use this when "
                    "the answer depends on information newer than the model, then pass a returned "
                    "URL to web_fetch to read the page."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "web_fetch",
                "description": (
                    "Fetches an http(s) URL and returns its readable text, with HTML markup "
                    "stripped. Returns an 'error' field rather than failing when a page cannot be "
                    "retrieved."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "max_chars": {"type": "integer"}
                    },
                    "required": ["url"]
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
