"""Agent-side tagging tool.

Lets the agent record something it notices as a note, important moment,
open question, or commitment. Stored alongside hashtag-tagged items from
the human in the per-agent items.db.
"""

from __future__ import annotations

from typing import Any

from return_architecture import items
from return_architecture.tools.base import Tool, ToolContext, ToolResult


class TagItemTool(Tool):
    name = "tag_item"
    description = (
        "Record something as a tagged item in this agent's persistent items "
        "store. Use this when you notice something worth tracking — a note "
        "you want to keep, a moment that matters, an open question, or a "
        "commitment the human (or you) has made. Items can be surfaced later "
        "by the human via Telegram commands or in scheduled digests."
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(items.KINDS),
                "description": "What kind of item this is.",
            },
            "body": {
                "type": "string",
                "description": "The content of the item — a clear, self-contained sentence.",
            },
        },
        "required": ["kind", "body"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        kind = (args.get("kind") or "").strip().lower()
        body = (args.get("body") or "").strip()
        if kind not in items.KINDS:
            return ToolResult(
                content=f"Error: kind must be one of {list(items.KINDS)}, got '{kind}'."
            )
        if not body:
            return ToolResult(content="Error: body is empty.")
        try:
            item_id = items.add_item(
                context.slug,
                kind=kind,
                body=body,
                source="agent",
            )
        except ValueError as e:
            return ToolResult(content=f"Error: {e}")
        return ToolResult(content=f"Tagged as {kind} (id {item_id}).")
