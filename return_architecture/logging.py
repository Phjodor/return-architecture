"""Append-only NDJSON conversation logging.

One file per agent per day under <agent>/logs/conversations-YYYY-MM-DD.ndjson.
Each line is a JSON object describing one event in the conversation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from return_architecture import paths


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def log_event(slug: str, event_type: str, payload: dict[str, Any]) -> None:
    paths.agent_logs_dir(slug).mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"ts": _now_iso(), "type": event_type, **payload},
        ensure_ascii=False,
    )
    path = paths.conversation_log_path(slug, _today())
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
