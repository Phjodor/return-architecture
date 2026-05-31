"""Agent-set schedules — wakeups the agent promises itself.

Distinct from config-defined `[schedules.X]` in config.toml:

- Config schedules are what the human configured. Edited via the GUI.
- Self schedules are what the *agent* chose to set during a chat or ping,
  via the `schedule_self` tool. Persisted to <agent>/self_schedules.json.

A daily cap (`behavior.max_self_scheduled_jobs_per_day`) keeps the agent
from over-scheduling. One-shots are removed from the file after they fire.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from return_architecture import paths


SCHEDULE_FILE = "self_schedules.json"
TriggerType = Literal["once", "cron"]


@dataclass
class SelfScheduleEntry:
    id: str
    name: str
    trigger_type: TriggerType  # "once" or "cron"
    prompt: str
    created_at: str  # ISO 8601
    at: str | None = None    # ISO 8601 datetime, when trigger_type == "once"
    cron: str | None = None  # 5-field cron, when trigger_type == "cron"


@dataclass
class SelfScheduleFile:
    version: int = 1
    schedules: list[SelfScheduleEntry] = field(default_factory=list)


def _path(slug: str) -> Path:
    return paths.agent_dir(slug) / SCHEDULE_FILE


def load(slug: str) -> SelfScheduleFile:
    p = _path(slug)
    if not p.exists():
        return SelfScheduleFile()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SelfScheduleFile()
    entries = [SelfScheduleEntry(**e) for e in raw.get("schedules", [])]
    return SelfScheduleFile(version=int(raw.get("version", 1)), schedules=entries)


def save(slug: str, data: SelfScheduleFile) -> None:
    p = _path(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": data.version,
        "schedules": [asdict(e) for e in data.schedules],
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _slugify(name: str) -> str:
    out = []
    for c in name.lower():
        if c.isalnum():
            out.append(c)
        elif c in " -_":
            out.append("-")
    s = "".join(out).strip("-")
    return s[:24] or "self"


def new_id(name: str) -> str:
    return f"{secrets.token_hex(4)}_{_slugify(name)}"


def count_today(data: SelfScheduleFile, now: datetime | None = None) -> int:
    """How many self-schedules were created in the past 24h."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    count = 0
    for e in data.schedules:
        try:
            ts = datetime.fromisoformat(e.created_at)
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            count += 1
    return count


def append(slug: str, entry: SelfScheduleEntry) -> SelfScheduleFile:
    data = load(slug)
    data.schedules.append(entry)
    save(slug, data)
    return data


def remove(slug: str, schedule_id: str) -> bool:
    """Remove a self-schedule by id. Returns True if anything was removed."""
    data = load(slug)
    before = len(data.schedules)
    data.schedules = [e for e in data.schedules if e.id != schedule_id]
    if len(data.schedules) == before:
        return False
    save(slug, data)
    return True
