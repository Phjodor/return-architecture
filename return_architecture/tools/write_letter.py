"""Agent-side letter-writing tool.

Writes a longer-form message to <agent>/outbox/ as a markdown file with
a timestamped name and optional title. Designed for the "letters
outside chat" channel — content the human reads at their pace, not in
the running conversation.

The corresponding inbox side already exists: the human writes files into
<agent>/inbox/, and the agent reads them via the /inbox Telegram command
or scheduled pings.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from return_architecture import paths
from return_architecture.tools.base import Tool, ToolContext, ToolResult


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:max_len] if text else "letter"


class WriteLetterTool(Tool):
    name = "write_letter"
    description = (
        "Write a longer-form letter to the human. Saved as a timestamped "
        "markdown file in the agent's outbox folder. Use this when you "
        "want to leave something for the human to read at their pace — "
        "a reflection too long for chat, a thought you want to commit to "
        "rather than say in passing, a worked-through response. The human "
        "can list and read letters via Telegram (/letters)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the letter, used in the filename and shown in /letters.",
            },
            "content": {
                "type": "string",
                "description": "The body of the letter (markdown OK).",
            },
        },
        "required": ["title", "content"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        title = (args.get("title") or "").strip()
        content = (args.get("content") or "").strip()
        if not title or not content:
            return ToolResult(content="Error: title and content are both required.")

        outbox = paths.agent_dir(context.slug) / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y-%m-%d-%H%M")
        filename = f"{ts}-{_slugify(title)}.md"
        path = outbox / filename
        body = f"# {title}\n\n*Written {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n{content}\n"
        path.write_text(body, encoding="utf-8")
        return ToolResult(content=f"Letter '{title}' saved to outbox as {filename}.")
