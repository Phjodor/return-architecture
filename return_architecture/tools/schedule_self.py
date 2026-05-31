"""Self-scheduling tools — the agent sets, lists, and cancels its own wakeups.

`schedule_self` adds either a one-shot ping at a specific moment OR a
recurring ping on a cron schedule. Daily cap from
`behavior.max_self_scheduled_jobs_per_day` is enforced.

`list_my_schedules` returns everything currently scheduled — config-defined
and self-set, so the agent can see the full picture.

`cancel_my_schedule` removes a self-schedule by id (config schedules are
not removable from here — those are the human's territory).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from return_architecture import config as cfg
from return_architecture import self_schedules as ra_self
from return_architecture.tools.base import Tool, ToolContext, ToolResult


def _no_scheduler() -> ToolResult:
    return ToolResult(content=(
        "Error: no live scheduler. This tool only works when running as the "
        "background daemon. (Self-schedules created from a one-shot chat "
        "session would not survive the chat exiting.)"
    ))


def _max_per_day(slug: str) -> int:
    try:
        return int(cfg.load_agent_config(slug).behavior.max_self_scheduled_jobs_per_day)
    except Exception:
        return 5


class ScheduleSelfTool(Tool):
    name = "schedule_self"
    description = (
        "Schedule a future ping for yourself — either a one-time wakeup at a "
        "specific moment, or a recurring rhythm. Use this when you want to "
        "return to something later: a thought worth coming back to, a "
        "rhythm you want to keep, a reminder you're choosing to set for "
        "yourself. "
        "Provide EXACTLY ONE of `at` (one-shot, ISO 8601 datetime in local "
        "time, e.g. '2026-05-31T19:00:00') or `cron` (recurring, 5-field cron "
        "expression, e.g. '0 7 * * 2' for every Tuesday at 7:00). "
        "Also provide a `prompt` — the text you'll receive when it fires "
        "(write it to your future self). `name` is a short human-readable "
        "label for your records. "
        "Each ping costs a model call; a daily cap on self-scheduling "
        "applies — be deliberate."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short label, e.g. 'check-in-on-X' or 'tuesday-rhythm'.",
            },
            "prompt": {
                "type": "string",
                "description": "What you'll read when the ping fires. Write to your future self.",
            },
            "at": {
                "type": "string",
                "description": "ISO 8601 datetime for a one-shot. Local time if no offset given.",
            },
            "cron": {
                "type": "string",
                "description": "5-field cron expression for a recurring schedule.",
            },
        },
        "required": ["name", "prompt"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.scheduler is None:
            return _no_scheduler()

        name = (args.get("name") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        at = (args.get("at") or "").strip()
        cron = (args.get("cron") or "").strip()

        if not name or not prompt:
            return ToolResult(content="Error: `name` and `prompt` are both required.")
        if bool(at) == bool(cron):
            return ToolResult(content=(
                "Error: provide exactly one of `at` (one-shot ISO datetime) "
                "or `cron` (recurring expression)."
            ))

        # Validate the trigger before persisting anything.
        if at:
            try:
                run_at = datetime.fromisoformat(at)
            except ValueError as e:
                return ToolResult(content=f"Error: `at` is not a valid ISO 8601 datetime ({e}).")
            ref = datetime.now(run_at.tzinfo) if run_at.tzinfo else datetime.now()
            if run_at <= ref:
                return ToolResult(content="Error: `at` must be in the future.")
        else:
            try:
                _validate_cron(cron)
            except ValueError as e:
                return ToolResult(content=f"Error: invalid cron — {e}")

        # Enforce the daily cap.
        data = ra_self.load(context.slug)
        used = ra_self.count_today(data)
        cap = _max_per_day(context.slug)
        if used >= cap:
            return ToolResult(content=(
                f"Error: daily self-schedule cap reached ({used}/{cap}). "
                f"Cancel one with `cancel_my_schedule` or wait until "
                f"the rolling 24h window opens."
            ))

        entry = ra_self.SelfScheduleEntry(
            id=ra_self.new_id(name),
            name=name,
            trigger_type="once" if at else "cron",
            prompt=prompt,
            created_at=datetime.now(timezone.utc).isoformat(),
            at=at or None,
            cron=cron or None,
        )

        try:
            ra_self.append(context.slug, entry)
            context.scheduler.add_self_job(entry)
        except Exception as e:
            # If add_self_job fails after persist, drop the JSON entry so
            # state stays consistent.
            ra_self.remove(context.slug, entry.id)
            return ToolResult(content=f"Error: could not register schedule: {e}")

        if at:
            return ToolResult(content=(
                f"Scheduled '{name}' (id: {entry.id}) for {at}. "
                f"You'll get the prompt when it fires; it then removes itself."
            ))
        return ToolResult(content=(
            f"Scheduled '{name}' (id: {entry.id}) on cron `{cron}`. "
            f"Use cancel_my_schedule with the id to stop it."
        ))


class ListMySchedulesTool(Tool):
    name = "list_my_schedules"
    description = (
        "List everything currently scheduled — both your self-set schedules "
        "(set via schedule_self) and the human-configured ones from your "
        "config. Useful before deciding whether to add a new one."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        lines: list[str] = []

        # Config schedules.
        try:
            agent_cfg = cfg.load_agent_config(context.slug)
            cfg_schedules = list(agent_cfg.schedules.items())
        except Exception as e:
            cfg_schedules = []
            lines.append(f"(could not load config schedules: {e})")

        if cfg_schedules:
            lines.append("── Configured (set by the human) ──")
            for name, entry in cfg_schedules:
                status = "enabled" if entry.enabled else "disabled"
                lines.append(f"  - {name} [{entry.kind}, {status}, cron={entry.cron!r}]")
        else:
            lines.append("(no config-defined schedules)")

        # Self schedules.
        data = ra_self.load(context.slug)
        if data.schedules:
            lines.append("")
            lines.append("── Your self-set schedules ──")
            for s in data.schedules:
                if s.trigger_type == "once":
                    when_str = f"once at {s.at}"
                else:
                    when_str = f"cron {s.cron!r}"
                lines.append(f"  - id={s.id} name={s.name!r} ({when_str})")
                lines.append(f"    prompt: {s.prompt[:120]}{'…' if len(s.prompt) > 120 else ''}")
        else:
            lines.append("")
            lines.append("(no self-set schedules)")

        cap = _max_per_day(context.slug)
        used = ra_self.count_today(data)
        lines.append("")
        lines.append(f"Daily self-schedule cap used: {used}/{cap}")

        return ToolResult(content="\n".join(lines))


class CancelMyScheduleTool(Tool):
    name = "cancel_my_schedule"
    description = (
        "Cancel one of your self-set schedules by its id (from "
        "list_my_schedules). Config-defined schedules are not cancellable "
        "from here — those are the human's territory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "The id of the self-schedule to cancel.",
            },
        },
        "required": ["id"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.scheduler is None:
            return _no_scheduler()
        schedule_id = (args.get("id") or "").strip()
        if not schedule_id:
            return ToolResult(content="Error: `id` is required.")

        removed_from_file = ra_self.remove(context.slug, schedule_id)
        removed_from_scheduler = context.scheduler.cancel_self_job(schedule_id)

        if not removed_from_file and not removed_from_scheduler:
            return ToolResult(content=f"Error: no self-schedule with id {schedule_id!r} found.")
        return ToolResult(content=f"Cancelled self-schedule {schedule_id}.")


def _validate_cron(expr: str) -> None:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("expected 5 fields (minute hour day month day-of-week)")
    minute, hour, day, month, dow = parts
    CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)
