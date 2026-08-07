from typing import Dict, Any, List

class MCPToolRegistry:
    """Provides Tool definitions for Model Context Protocol consumers."""

    @classmethod
    def get_tool_definitions(cls) -> List[Dict[str, Any]]:
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
