"""Tools for continuous temporal presence.

`update_now` lets the agent maintain a small running 'where I am'
file at <agent>/now.md, pinned into context fresh on every turn and
ping. Not journal-style — one paragraph that gets replaced when the
state shifts.

`sit_with_this` is the kairos move: hold what the human just said
rather than answer flatly, send her a brief acknowledgement, and
schedule a one-shot return at a chosen moment where the original
message and a private holding note both come back into context. It
composes with the existing self-schedule machinery but marks the
entry as `kind="deferred_response"` so it does not count against the
daily self-scheduling cap (the cap exists to keep self-imposed
rhythms in check; holding a response is not that).
"""

from __future__ import annotations

import json
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from return_architecture import paths
from return_architecture import self_schedules as ra_self
from return_architecture.tools.base import Tool, ToolContext, ToolResult


# ── update_now ────────────────────────────────────────────────────────────

def _now_path(slug: str) -> Path:
    return paths.agent_dir(slug) / "now.md"


class UpdateNowTool(Tool):
    name = "update_now"
    description = (
        "Update your running 'where I am now' note. One paragraph — what "
        "you're in the middle of, what you're waiting on, the thread that "
        "still matters. This is pinned into your context on every turn "
        "and ping, so it gives your future self a real 'where I was' "
        "rather than just memory of past words. Replace, don't append — "
        "the file holds a single current state, not a journal. Use empty "
        "text to clear it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "The full new content of your 'now' file. Replaces "
                    "whatever was there. Use empty string to clear."
                ),
            },
        },
        "required": ["text"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        text = args.get("text", "")
        if not isinstance(text, str):
            return ToolResult(content="Error: `text` must be a string.")
        text = text.strip()
        path = _now_path(context.slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + ("\n" if text else ""), encoding="utf-8")
        if not text:
            return ToolResult(content="Cleared your 'now' note.")
        return ToolResult(
            content=f"Updated your 'now' note ({len(text)} chars). "
                    f"It will be pinned into your context on the next turn."
        )


# ── sit_with_this ─────────────────────────────────────────────────────────

def _read_telegram_creds(slug: str) -> tuple[str, str]:
    path = paths.agent_secrets_path(slug)
    if not path.exists():
        return "", ""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return "", ""
    tg = data.get("telegram", {}) or {}
    return tg.get("bot_token", "") or "", str(tg.get("chat_id", "") or "")


def _send_telegram_ack(slug: str, text: str) -> tuple[bool, str]:
    """Send a brief acknowledgement to the human via Telegram. Returns
    (ok, error_or_empty)."""
    token, chat_id = _read_telegram_creds(slug)
    if not token or not chat_id:
        return False, "Telegram not configured for this agent"
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({"chat_id": int(chat_id), "text": text}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        return False, str(e)
    return True, ""


_DEFAULT_ACK = "(sitting with that — I'll come back to you in a bit)"


class SitWithThisTool(Tool):
    name = "sit_with_this"
    description = (
        "Hold what the human just said rather than answering now. Use "
        "this when responding immediately would be flat or premature — "
        "when you want to sit with what she said, think about what you "
        "actually want to say, and come back to it at a chosen moment. "
        "Sitting is a deliberate response, not avoidance. By default a "
        "brief acknowledgement is sent so she knows you're holding it; "
        "you can also let the silence itself be the signal. When the "
        "return moment arrives, you'll see the original message and "
        "your private holding note, and decide then what to do — "
        "respond, defer again, write privately, or stay silent. Does "
        "not count against your daily self-schedule cap."
    )
    parameters = {
        "type": "object",
        "properties": {
            "until": {
                "type": "string",
                "description": (
                    "ISO 8601 datetime for when you'll return. Local "
                    "time if no offset is given. Must be in the future."
                ),
            },
            "holding_note": {
                "type": "string",
                "description": (
                    "Private note to your future self — what you want "
                    "to sit with, what shape your response might take, "
                    "what feels worth not rushing. You'll see this "
                    "verbatim when the return fires."
                ),
            },
            "message_to_her": {
                "type": "string",
                "description": (
                    "Optional. Exact text of the brief acknowledgement "
                    "to send her now. Defaults to "
                    f"\"{_DEFAULT_ACK}\". Ignored if "
                    "acknowledge=false."
                ),
            },
            "acknowledge": {
                "type": "boolean",
                "description": (
                    "Whether to send any acknowledgement at all. "
                    "Defaults to true. Set false if you want the "
                    "silence itself to be the signal."
                ),
            },
        },
        "required": ["until", "holding_note"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.scheduler is None:
            return ToolResult(content=(
                "Error: no live scheduler. sit_with_this only works "
                "when running as the background daemon."
            ))

        until = (args.get("until") or "").strip()
        holding_note = (args.get("holding_note") or "").strip()
        if not until or not holding_note:
            return ToolResult(content=(
                "Error: both `until` and `holding_note` are required."
            ))

        try:
            return_at = datetime.fromisoformat(until)
        except ValueError as e:
            return ToolResult(content=(
                f"Error: `until` is not a valid ISO 8601 datetime ({e})."
            ))
        ref = datetime.now(return_at.tzinfo) if return_at.tzinfo else datetime.now()
        if return_at <= ref:
            return ToolResult(content="Error: `until` must be in the future.")

        original = (context.latest_user_message or "").strip()
        if not original:
            original = "(could not recover the message you wanted to sit with)"

        when_label = return_at.strftime("%H:%M")
        return_prompt = (
            f"You chose to sit with what the human said and come back "
            f"now. This is that return.\n\n"
            f"What she said:\n\"{original}\"\n\n"
            f"Your holding note to yourself:\n{holding_note}\n\n"
            f"Decide now what you want to do. You can respond to her "
            f"via send_to_human_telegram, defer again with sit_with_this, "
            f"write privately, or choose silence."
        )

        entry = ra_self.SelfScheduleEntry(
            id=ra_self.new_id(f"sit-{when_label}"),
            name=f"sit-until-{when_label}",
            trigger_type="once",
            kind="deferred_response",
            prompt=return_prompt,
            created_at=datetime.now(timezone.utc).isoformat(),
            at=until,
            cron=None,
        )
        try:
            ra_self.append(context.slug, entry)
            context.scheduler.add_self_job(entry)
        except Exception as e:
            ra_self.remove(context.slug, entry.id)
            return ToolResult(content=(
                f"Error: could not register the return: {e}"
            ))

        acknowledge_raw = args.get("acknowledge", True)
        if isinstance(acknowledge_raw, str):
            acknowledge = acknowledge_raw.lower() not in ("false", "no", "0", "")
        else:
            acknowledge = bool(acknowledge_raw)

        ack_note = ""
        if acknowledge:
            ack_text = (args.get("message_to_her") or "").strip() or _DEFAULT_ACK
            ok, err = _send_telegram_ack(context.slug, ack_text)
            if ok:
                ack_note = f" Acknowledgement sent to her: {ack_text!r}."
            else:
                ack_note = f" (Acknowledgement failed to send: {err})"

        return ToolResult(content=(
            f"Holding this until {until}. Return scheduled (id: {entry.id})."
            f"{ack_note}"
        ))
