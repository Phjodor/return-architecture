"""Scheduling for an agent.

Uses APScheduler's AsyncIOScheduler so jobs run in the same asyncio event
loop as the Telegram worker — no threads, no cross-thread locking. A
single asyncio.Lock around runtime.turn()/ping() prevents concurrent
modification of the in-memory message history.

Schedule definitions live in the agent's config.toml under [schedules.X].
On scheduler start, every enabled entry becomes a CronTrigger job.

In-memory jobstore for v1. Persistent jobstore arrives with `schedule_self`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from return_architecture import config as cfg
from return_architecture import logging as ralog
from return_architecture import question_sessions as ra_qs
from return_architecture import reflective_review as ra_reflect
from return_architecture import runtime
from return_architecture import self_schedules as ra_self
from return_architecture import summaries as ra_summaries


logger = logging.getLogger("return_architecture.scheduling")


@dataclass
class ScheduleHandle:
    name: str
    cron: str
    enabled: bool


class AgentScheduler:
    def __init__(self, session: runtime.AgentSession, turn_lock: asyncio.Lock) -> None:
        self._session = session
        self._lock = turn_lock
        self._scheduler = AsyncIOScheduler()
        self._handles: list[ScheduleHandle] = []

    def register_from_config(self) -> list[ScheduleHandle]:
        for name, entry in self._session.config.schedules.items():
            handle = ScheduleHandle(name=name, cron=entry.cron, enabled=entry.enabled)
            self._handles.append(handle)
            if not entry.enabled:
                continue
            trigger = _parse_cron(entry.cron)
            self._scheduler.add_job(
                self._run_ping,
                trigger=trigger,
                id=f"ping:{name}",
                kwargs={
                    "ping_name": name,
                    "prompt": entry.prompt,
                    "kind": entry.kind,
                },
                replace_existing=True,
                misfire_grace_time=300,
            )
        return self._handles

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def listed_handles(self) -> list[ScheduleHandle]:
        return list(self._handles)

    # ── Self-schedule integration ─────────────────────────────────────────

    def register_self_schedules(self) -> int:
        """Load <agent>/self_schedules.json and add each entry to the live
        scheduler. Returns how many were registered."""
        data = ra_self.load(self._session.slug)
        added = 0
        for entry in data.schedules:
            try:
                self._add_self_job(entry)
                added += 1
            except Exception as e:
                print(
                    f"[scheduler] could not register self-schedule "
                    f"{entry.id!r}: {e}",
                    flush=True,
                )
        return added

    def add_self_job(self, entry: "ra_self.SelfScheduleEntry") -> None:
        """Add a self-schedule to the live scheduler (called by the tool
        after persisting to JSON)."""
        self._add_self_job(entry)

    def cancel_self_job(self, schedule_id: str) -> bool:
        """Remove a self-schedule from the live scheduler. Returns True if
        anything was removed."""
        job_id = f"self:{schedule_id}"
        try:
            self._scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    def _add_self_job(self, entry: "ra_self.SelfScheduleEntry") -> None:
        if entry.trigger_type == "once":
            if not entry.at:
                raise ValueError("one-shot self-schedule needs `at`")
            run_at = datetime.fromisoformat(entry.at)
            # Skip jobs whose time has already passed (likely after a
            # daemon downtime). Leave the JSON entry as a record but don't
            # register it.
            if run_at <= datetime.now(run_at.tzinfo):
                # Garbage-collect stale one-shots so the file stays tidy.
                ra_self.remove(self._session.slug, entry.id)
                return
            trigger = DateTrigger(run_date=run_at)
        elif entry.trigger_type == "cron":
            if not entry.cron:
                raise ValueError("recurring self-schedule needs `cron`")
            trigger = _parse_cron(entry.cron)
        else:
            raise ValueError(f"unknown trigger_type: {entry.trigger_type!r}")

        self._scheduler.add_job(
            self._run_ping,
            trigger=trigger,
            id=f"self:{entry.id}",
            kwargs={
                "ping_name": f"self:{entry.name}",
                "prompt": entry.prompt,
                "kind": "regular",
                "self_schedule_id": entry.id if entry.trigger_type == "once" else None,
            },
            replace_existing=True,
            misfire_grace_time=300,
        )

    async def _run_ping(self, *, ping_name: str, prompt: str,
                        kind: str = "regular",
                        self_schedule_id: str | None = None) -> None:
        print(f"[scheduler] ping '{ping_name}' firing (kind={kind})...", flush=True)

        if kind == "question_session":
            async with self._lock:
                try:
                    result = await asyncio.to_thread(
                        ra_qs.run_session, self._session.slug
                    )
                except Exception as e:
                    print(f"[scheduler] question session '{ping_name}' errored: {e}", flush=True)
                    ralog.log_event(self._session.slug, "scheduled_ping_error", {
                        "ping_name": ping_name, "kind": kind, "error": repr(e),
                    })
                    return
            print(
                f"[scheduler] question session '{ping_name}' done: "
                f"answered={result.answered}, skipped={result.skipped}",
                flush=True,
            )
            return

        if kind == "question_pattern":
            async with self._lock:
                try:
                    pattern_result = await asyncio.to_thread(
                        ra_qs.run_pattern_recap, self._session.slug
                    )
                except Exception as e:
                    print(f"[scheduler] question pattern '{ping_name}' errored: {e}", flush=True)
                    ralog.log_event(self._session.slug, "scheduled_ping_error", {
                        "ping_name": ping_name, "kind": kind, "error": repr(e),
                    })
                    return
            if pattern_result is None:
                print(f"[scheduler] question pattern '{ping_name}' skipped (not enough responses)", flush=True)
            else:
                print(
                    f"[scheduler] question pattern '{ping_name}' done: "
                    f"{pattern_result.answered_count} answered, "
                    f"{pattern_result.skipped_count} skipped in window",
                    flush=True,
                )
            return

        if kind == "reflective_interruption":
            async with self._lock:
                try:
                    review = await asyncio.to_thread(
                        ra_reflect.run_review, self._session.slug
                    )
                except Exception as e:
                    print(f"[scheduler] reflective review '{ping_name}' errored: {e}", flush=True)
                    ralog.log_event(self._session.slug, "scheduled_ping_error", {
                        "ping_name": ping_name, "kind": kind, "error": repr(e),
                    })
                    return
            if review.ran:
                print(f"[scheduler] reflective review '{ping_name}' done: {review.folder}", flush=True)
            else:
                print(f"[scheduler] reflective review '{ping_name}' skipped: {review.reason}", flush=True)
            return

        final_prompt = prompt
        if kind in ra_summaries.SUMMARY_KINDS:
            lookback = ra_summaries.SUMMARY_KINDS[kind]
            try:
                context = await asyncio.to_thread(
                    ra_summaries.build_summary_context, self._session.slug, lookback
                )
                final_prompt = ra_summaries.render_ping_prompt(prompt, context)
            except Exception as e:
                print(f"[scheduler] summary context build failed for '{ping_name}': {e}", flush=True)
        async with self._lock:
            try:
                result = await asyncio.to_thread(
                    runtime.ping, self._session, ping_name, final_prompt
                )
            except Exception as e:
                print(f"[scheduler] ping '{ping_name}' errored: {e}", flush=True)
                ralog.log_event(self._session.slug, "scheduled_ping_error", {
                    "ping_name": ping_name,
                    "error": repr(e),
                })
                if self_schedule_id:
                    ra_self.remove(self._session.slug, self_schedule_id)
                return
        if result:
            preview = result[:200] + ("..." if len(result) > 200 else "")
            print(f"[scheduler] ping '{ping_name}' produced text: {preview}", flush=True)
        else:
            print(f"[scheduler] ping '{ping_name}' completed (silence or tool-only)", flush=True)
        if self_schedule_id:
            ra_self.remove(self._session.slug, self_schedule_id)


def _parse_cron(expr: str) -> CronTrigger:
    """Accept standard 5-field cron: minute hour day month day-of-week."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(
            f"Invalid cron expression '{expr}' — expected 5 fields "
            f"(minute hour day month day-of-week)."
        )
    minute, hour, day, month, dow = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=dow,
    )


def list_schedules(slug: str) -> list[ScheduleHandle]:
    """Read an agent's schedule definitions without starting the scheduler."""
    agent_cfg = cfg.load_agent_config(slug)
    return [
        ScheduleHandle(name=name, cron=entry.cron, enabled=entry.enabled)
        for name, entry in agent_cfg.schedules.items()
    ]
