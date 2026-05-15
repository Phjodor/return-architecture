"""Context builders for summary pings.

Gathers a period's conversation excerpts, items, and artifact exchanges
into a context block the scheduler prepends to a ping prompt. The agent
then decides what (if anything) to compose for the human.

Three lookback windows ship: daily (1 day), weekly (7 days), monthly
(30 days). Each variant also includes the *all-time* open items, since
those don't have a temporal scope and matter for any reflective moment.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from return_architecture import items as ra_items
from return_architecture import paths


SUMMARY_KINDS = {
    "daily_summary":   1,
    "weekly_summary":  7,
    "monthly_summary": 30,
}

# Cap to keep prompts from ballooning on long histories.
MAX_CONVERSATION_LINES = 40
MAX_CONVERSATION_CHARS = 6000


def build_summary_context(slug: str, lookback_days: int) -> str:
    """Return a single block of text summarising the last N days for the agent.

    The block has labelled sections: conversation excerpts, items created
    in the window, open items overall, and artifact exchanges in the
    window. Empty sections are omitted.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    since_iso = since.isoformat()

    parts: list[str] = []

    conv = _gather_conversation(slug, since)
    if conv:
        parts.append("Conversations from this period:\n" + conv)

    period_items = _gather_items_since(slug, since_iso)
    if period_items:
        parts.append("Items tagged in this period:\n" + period_items)

    open_items = _gather_open_items(slug)
    if open_items:
        parts.append("Open items overall:\n" + open_items)

    artifacts = _gather_artifacts_since(slug, since)
    if artifacts:
        parts.append("Artifact exchanges this period:\n" + artifacts)

    if not parts:
        return "(no activity in the period — the agent and human have been quiet)"
    return "\n\n".join(parts)


def _gather_conversation(slug: str, since: datetime) -> str:
    logs_dir = paths.agent_logs_dir(slug)
    if not logs_dir.exists():
        return ""
    files = sorted(logs_dir.glob("conversations-*.ndjson"))
    lines: list[str] = []
    total_chars = 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = ev.get("ts", "")
                    if ts < since.isoformat():
                        continue
                    label, body = _format_event(ev)
                    if not body:
                        continue
                    short = body if len(body) <= 400 else body[:400] + "…"
                    line_out = f"[{ts[:16].replace('T', ' ')}] {label}: {short}"
                    lines.append(line_out)
                    total_chars += len(line_out)
                    if total_chars >= MAX_CONVERSATION_CHARS:
                        break
        except OSError:
            continue
        if total_chars >= MAX_CONVERSATION_CHARS:
            break

    if len(lines) > MAX_CONVERSATION_LINES:
        omitted = len(lines) - MAX_CONVERSATION_LINES
        lines = lines[-MAX_CONVERSATION_LINES:]
        lines.insert(0, f"(…{omitted} earlier lines omitted)")
    return "\n".join(lines)


def _format_event(ev: dict) -> tuple[str, str]:
    etype = ev.get("type", "")
    if etype == "user_message":
        return "human", ev.get("content") or ""
    if etype == "assistant_message":
        return "you", ev.get("text") or ""
    if etype == "scheduled_ping":
        return "[ping]", ev.get("ping_name", "?")
    if etype == "telegram_message_in":
        return "human (tg)", ev.get("text") or ""
    if etype == "telegram_message_out":
        return "you (tg)", ev.get("text") or ""
    return "", ""


def _gather_items_since(slug: str, since_iso: str) -> str:
    rows = ra_items.list_items(slug, status=None, limit=500)
    relevant = [r for r in rows if r.created_at >= since_iso]
    if not relevant:
        return ""
    relevant.sort(key=lambda r: r.created_at)
    return "\n".join(
        f"- ({r.kind}, by {r.source}) [{r.created_at[:10]}] {r.body[:300]}"
        for r in relevant
    )


def _gather_open_items(slug: str) -> str:
    rows = ra_items.list_items(slug, status="open", limit=200)
    if not rows:
        return ""
    rows.sort(key=lambda r: (r.kind, r.created_at))
    return "\n".join(
        f"- ({r.kind}) [{r.created_at[:10]}] #{r.id} {r.body[:300]}"
        for r in rows
    )


def _gather_artifacts_since(slug: str, since: datetime) -> str:
    artifacts_dir = paths.agent_dir(slug) / "artifacts"
    if not artifacts_dir.exists():
        return ""
    cutoff_prefix = since.strftime("%Y-%m-%d")
    lines: list[str] = []
    for child in sorted(artifacts_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name in ("incoming", "notes", "shared"):
            continue
        # Folder names start with YYYY-MM-DD-HHMM-...
        if child.name[:10] < cutoff_prefix:
            continue
        decision = _peek_decision(child)
        lines.append(f"- {child.name}" + (f" (decision: {decision})" if decision else ""))
    return "\n".join(lines)


def _peek_decision(exchange_dir: Path) -> str:
    meta = exchange_dir / "meta.toml"
    if not meta.exists():
        return ""
    try:
        import tomllib
        with open(meta, "rb") as f:
            data = tomllib.load(f)
        return str(data.get("decision", ""))
    except Exception:
        return ""


def render_ping_prompt(base_prompt: str, context: str) -> str:
    """Combine a context block with the schedule's prompt."""
    return (
        "Here is context from this period, gathered for your reflection. "
        "This is not a transcript you must summarise point by point — it's "
        "material for you to look back at. If something stands out, name it. "
        "If you have nothing pressing, choose silence.\n\n"
        f"{context}\n\n"
        f"---\n\n"
        f"{base_prompt}"
    )
