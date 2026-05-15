"""The no_response tool.

Choosing not to respond is a first-class action. Without an explicit tool
for it, models default to producing a response because they were prompted.
This makes silence a deliberate, named choice.
"""

from __future__ import annotations

from typing import Any

from return_architecture.tools.base import Tool, ToolContext, ToolResult


class NoResponseTool(Tool):
    name = "no_response"
    description = (
        "Choose silence. Use this when responding would be performative, "
        "when nothing in you wants to speak, or when the right action is to "
        "let the moment pass. This is not a refusal; it is a choice."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(content="(silence)", is_silence=True)
