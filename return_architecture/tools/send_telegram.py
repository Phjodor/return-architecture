"""Send a message to the human via Telegram.

This tool is for agent-initiated messages — typically during scheduled
pings, where there's no incoming Telegram message whose reply would be
auto-sent. The agent must explicitly choose to reach out.

When the agent is replying to a Telegram message (i.e., a turn triggered
by the worker's message handler), the worker auto-sends the text reply.
Using this tool *in that context* would result in a second message; the
system prompt should teach the distinction.
"""

from __future__ import annotations

import tomllib
from typing import Any

import httpx

from return_architecture import paths
from return_architecture.tools.base import Tool, ToolContext, ToolResult


class SendToHumanTelegramTool(Tool):
    name = "send_to_human_telegram"
    description = (
        "Send a message to the human via Telegram. Use this only to initiate "
        "contact when there is no incoming message to reply to — for example, "
        "during a scheduled ping, when you have decided to reach out. "
        "When you are responding to a message the human just sent, do NOT "
        "use this tool; simply reply with text and it will be delivered."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The text to send to the human.",
            },
        },
        "required": ["message"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        message = (args.get("message") or "").strip()
        if not message:
            return ToolResult(content="Error: empty message, nothing sent.")

        token, chat_id = _read_telegram_creds(context.slug)
        if not token or not chat_id:
            return ToolResult(content="Error: Telegram is not configured for this agent.")

        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=10.0,
            )
            data = resp.json()
        except Exception as e:
            return ToolResult(content=f"Error sending Telegram message: {e}")

        if not data.get("ok"):
            return ToolResult(content=f"Telegram API error: {data.get('description', 'unknown')}")
        return ToolResult(content="Message sent to human via Telegram.")


def _read_telegram_creds(slug: str) -> tuple[str, str]:
    path = paths.agent_secrets_path(slug)
    if not path.exists():
        return "", ""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    tg = data.get("telegram", {}) or {}
    return tg.get("bot_token", "") or "", str(tg.get("chat_id", "") or "")
