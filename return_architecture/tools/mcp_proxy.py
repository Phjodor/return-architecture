"""Thin Tool wrapper around an MCP server's exposed tool.

Looks identical to a built-in tool from the runtime's perspective.
"""

from __future__ import annotations

from typing import Any

from return_architecture.mcp_client import MCPServer
from return_architecture.tools.base import Tool, ToolContext, ToolResult


class MCPProxyTool(Tool):
    def __init__(self, server: MCPServer, name: str, description: str, parameters: dict) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._server = server

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            output = self._server.call_tool(self.name, args)
        except Exception as e:
            return ToolResult(content=f"Error calling MCP tool '{self.name}': {e}")
        return ToolResult(content=output)
