"""Schedules editor — list, create, edit, delete per-agent schedules.

Cron picker supports four patterns (Daily / Weekly / Monthly / Custom)
with round-trip parsing of existing cron strings. The "Custom" fallback
covers anything more complex.

Note on day-of-week: APScheduler (which actually runs the cron jobs) uses
0=Monday through 6=Sunday — NOT standard cron's 0=Sunday convention. The
picker uses APScheduler's numbering. Existing schedules in config that
used 0=Sunday will appear as Monday in the picker; this reflects the
behaviour rather than the original intent.
"""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from return_architecture import service as ra_service
from return_architecture.gui import helpers


SCHEDULE_KINDS = [
    "regular",
    "daily_summary",
    "weekly_summary",
    "monthly_summary",
    "question_session",
    "question_pattern",
]

KIND_HELP = {
    "regular":          "Open-ended ping; the prompt is delivered to the agent as-is.",
    "daily_summary":    "Prepends yesterday's context to the prompt (conversation excerpts, items, open items, artifact exchanges).",
    "weekly_summary":   "Prepends the last 7 days of context to the prompt.",
    "monthly_summary":  "Prepends the last 30 days of context to the prompt.",
    "question_session": "Runs a curated batch of questions from the bank. The prompt field is unused.",
    "question_pattern": "Observer recap of recent question responses (looking back 14 days). The prompt field is unused.",
}

PATTERNS = ["Daily", "Weekly", "Monthly", "Every N days", "Custom"]

PROMPT_EXAMPLE = (
    "It's morning. You can send a brief greeting via send_to_human_telegram, "
    "write a longer letter via write_letter, write privately in your "
    "reflection space, schedule yourself a follow-up moment, or stay silent. "
    "Pick what feels right."
)
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def render() -> None:
    slug = st.session_state.get("agent_slug")
    if not slug:
        st.warning("No agent selected. Use the sidebar.")
        return

    st.title(f"Schedules — {slug}")
    st.caption(
        "Schedules wake the agent at specific times. Each one has a kind "
        "(which controls what context is prepended), a cron expression, "
        "and an optional prompt. Changes take effect after a service reload."
    )

    config = helpers.load_agent_config_raw(slug)
    schedules: dict[str, Any] = config.get("schedules", {}) or {}

    # Add-schedule control
    if st.session_state.get("_add_schedule_open"):
        _render_add_form(slug, config, schedules)
    else:
        if st.button("➕ Add schedule", key="_add_schedule_btn"):
            st.session_state["_add_schedule_open"] = True
            st.rerun()

    st.divider()

    if not schedules:
        st.info("No schedules defined yet.")
    else:
        for name in list(schedules.keys()):
            entry = schedules[name]
            with st.expander(_summarize(name, entry), expanded=False):
                _render_editor(slug, config, name, entry)

    st.divider()

    # Global reload button — schedules need a service restart to take effect.
    if st.button("Reload service to apply changes", key="_reload_for_schedules"):
        try:
            with st.spinner("Restarting service…"):
                ra_service.restart(slug)
            st.success("Service restarted.")
        except (RuntimeError, FileNotFoundError) as e:
            st.error(f"Restart failed: {e}")


# ── Summary line for the expander header ──────────────────────────────────

def _summarize(name: str, entry: dict) -> str:
    marker = "✓" if entry.get("enabled") else "✗"
    kind = entry.get("kind", "regular")
    cron = entry.get("cron", "")
    return f"{marker} {name} · {kind} · `{cron}`"


# ── Editor for one existing schedule ──────────────────────────────────────

def _render_editor(slug: str, config: dict, name: str, entry: dict) -> None:
    enabled = st.checkbox(
        "Enabled",
        value=bool(entry.get("enabled", False)),
        key=f"_sched_enabled_{name}",
    )

    kind = st.selectbox(
        "Kind",
        options=SCHEDULE_KINDS,
        index=SCHEDULE_KINDS.index(entry.get("kind", "regular"))
            if entry.get("kind", "regular") in SCHEDULE_KINDS else 0,
        help=KIND_HELP.get(entry.get("kind", "regular"), ""),
        key=f"_sched_kind_{name}",
    )
    st.caption(KIND_HELP.get(kind, ""))

    new_cron = _cron_picker(entry.get("cron", "0 9 * * *"), key_prefix=f"sched_{name}")

    prompt_unused = kind in ("question_session", "question_pattern")
    if prompt_unused:
        st.caption(
            "This kind has its own built-in behavior — no prompt is needed. "
            "The agent's instructions live in the runtime."
        )
        new_prompt = entry.get("prompt", "")
    else:
        st.caption(
            "All enabled tools are available when this ping fires. The agent "
            "can also stay silent (the `no_response` tool is always on). "
            "Mentioning a tool by name in your prompt suggests it; not "
            "mentioning a tool doesn't block it."
        )
        new_prompt = st.text_area(
            "Prompt — what the agent receives at this moment",
            value=entry.get("prompt", ""),
            height=140,
            placeholder=PROMPT_EXAMPLE,
            key=f"_sched_prompt_{name}",
        )

    cols = st.columns([1, 1, 5])
    if cols[0].button("Save", key=f"_sched_save_{name}"):
        config.setdefault("schedules", {})[name] = {
            "enabled": bool(enabled),
            "cron":    new_cron,
            "kind":    kind,
            "prompt":  new_prompt,
        }
        helpers.write_agent_config(slug, config)
        st.success("Saved. Reload the service below for changes to take effect.")

    if cols[1].button("Delete", key=f"_sched_del_btn_{name}"):
        st.session_state[f"_confirm_del_sched_{name}"] = True
        st.rerun()

    if st.session_state.get(f"_confirm_del_sched_{name}"):
        st.warning(f"Delete the schedule **{name}**? This can't be undone.")
        confirm_cols = st.columns([1, 1, 5])
        if confirm_cols[0].button("Yes, delete", key=f"_confirm_yes_sched_{name}"):
            config.get("schedules", {}).pop(name, None)
            helpers.write_agent_config(slug, config)
            st.session_state[f"_confirm_del_sched_{name}"] = False
            st.success(f"Deleted '{name}'.")
            st.rerun()
        if confirm_cols[1].button("Cancel", key=f"_confirm_no_sched_{name}"):
            st.session_state[f"_confirm_del_sched_{name}"] = False
            st.rerun()


# ── Add-schedule form ─────────────────────────────────────────────────────

def _render_add_form(slug: str, config: dict, schedules: dict) -> None:
    st.subheader("New schedule")
    name = st.text_input(
        "Name (lowercase letters, digits, underscores; starts with a letter)",
        value="",
        key="_new_sched_name",
        placeholder="e.g. evening_check",
    )
    kind = st.selectbox(
        "Kind",
        options=SCHEDULE_KINDS,
        index=0,
        key="_new_sched_kind",
    )
    st.caption(KIND_HELP.get(kind, ""))

    cron = _cron_picker("0 9 * * *", key_prefix="new_sched")

    prompt_unused = kind in ("question_session", "question_pattern")
    if prompt_unused:
        st.caption(
            "This kind has its own built-in behavior — no prompt is needed. "
            "The agent's instructions live in the runtime."
        )
        prompt = ""
    else:
        st.caption(
            "All enabled tools are available when this ping fires. The agent "
            "can also stay silent (the `no_response` tool is always on). "
            "Mentioning a tool by name in your prompt suggests it; not "
            "mentioning a tool doesn't block it."
        )
        prompt = st.text_area(
            "Prompt",
            value="",
            height=140,
            placeholder=PROMPT_EXAMPLE,
            key="_new_sched_prompt",
        )

    cols = st.columns([1, 1, 5])
    if cols[0].button("Create", key="_create_sched"):
        if not _SLUG_RE.match(name or ""):
            st.error("Invalid name. Use lowercase letters, digits, and underscores only; must start with a letter.")
        elif name in schedules:
            st.error(f"A schedule named '{name}' already exists.")
        else:
            config.setdefault("schedules", {})[name] = {
                "enabled": False,
                "cron":    cron,
                "kind":    kind,
                "prompt":  prompt,
            }
            helpers.write_agent_config(slug, config)
            st.session_state["_add_schedule_open"] = False
            for k in ("_new_sched_name", "_new_sched_kind", "_new_sched_prompt"):
                st.session_state.pop(k, None)
            st.success(f"Created '{name}'. It starts disabled — open it below to enable.")
            st.rerun()
    if cols[1].button("Cancel", key="_cancel_new_sched"):
        st.session_state["_add_schedule_open"] = False
        st.rerun()


# ── Cron picker ───────────────────────────────────────────────────────────

def _cron_picker(initial: str, *, key_prefix: str) -> str:
    pattern = _classify(initial)
    fields = _parse_fields(initial)

    selected = st.radio(
        "Pattern",
        options=PATTERNS,
        index=PATTERNS.index(pattern),
        horizontal=True,
        key=f"_{key_prefix}_pattern",
    )

    if selected == "Daily":
        cols = st.columns(2)
        hour = cols[0].number_input(
            "Hour (0–23, 24-hour)",
            min_value=0, max_value=23,
            value=fields["hour"],
            key=f"_{key_prefix}_h",
        )
        minute = cols[1].number_input(
            "Minute (0–59)",
            min_value=0, max_value=59,
            value=fields["minute"],
            key=f"_{key_prefix}_m",
        )
        return f"{minute} {hour} * * *"

    if selected == "Weekly":
        cols = st.columns(3)
        day = cols[0].selectbox(
            "Day of week",
            options=DAY_NAMES,
            index=fields["dow"],
            key=f"_{key_prefix}_d",
            help="APScheduler uses 0=Monday … 6=Sunday.",
        )
        hour = cols[1].number_input(
            "Hour (0–23)",
            min_value=0, max_value=23,
            value=fields["hour"],
            key=f"_{key_prefix}_h",
        )
        minute = cols[2].number_input(
            "Minute (0–59)",
            min_value=0, max_value=59,
            value=fields["minute"],
            key=f"_{key_prefix}_m",
        )
        return f"{minute} {hour} * * {DAY_NAMES.index(day)}"

    if selected == "Monthly":
        cols = st.columns(3)
        dom = cols[0].number_input(
            "Day of month (1–31)",
            min_value=1, max_value=31,
            value=fields["dom"],
            key=f"_{key_prefix}_d",
        )
        hour = cols[1].number_input(
            "Hour (0–23)",
            min_value=0, max_value=23,
            value=fields["hour"],
            key=f"_{key_prefix}_h",
        )
        minute = cols[2].number_input(
            "Minute (0–59)",
            min_value=0, max_value=59,
            value=fields["minute"],
            key=f"_{key_prefix}_m",
        )
        return f"{minute} {hour} {dom} * *"

    if selected == "Every N days":
        cols = st.columns(3)
        n = cols[0].number_input(
            "Interval (days)",
            min_value=2, max_value=30,
            value=fields["every_n"],
            key=f"_{key_prefix}_n",
            help="2 = every other day, 3 = every third day, etc.",
        )
        hour = cols[1].number_input(
            "Hour (0–23)",
            min_value=0, max_value=23,
            value=fields["hour"],
            key=f"_{key_prefix}_h",
        )
        minute = cols[2].number_input(
            "Minute (0–59)",
            min_value=0, max_value=59,
            value=fields["minute"],
            key=f"_{key_prefix}_m",
        )
        return f"{minute} {hour} */{n} * *"

    # Custom
    st.caption(
        "Advanced: write a raw cron expression. Five fields separated by spaces: "
        "**minute hour day-of-month month day-of-week**. Use `*` to mean 'any'. "
        "For most schedules, the other patterns above are easier."
    )
    return st.text_input(
        "Cron expression",
        value=initial,
        key=f"_{key_prefix}_raw",
    ) or initial


def _classify(cron: str) -> str:
    parts = (cron or "").strip().split()
    if len(parts) != 5:
        return "Custom"
    m, h, d, mo, dow = parts
    # Daily: M H * * *
    if d == "*" and mo == "*" and dow == "*" and m.isdigit() and h.isdigit():
        return "Daily"
    # Weekly: M H * * <0-6>
    if d == "*" and mo == "*" and m.isdigit() and h.isdigit() and dow.isdigit():
        if 0 <= int(dow) <= 6:
            return "Weekly"
    # Monthly: M H <1-31> * *
    if mo == "*" and dow == "*" and m.isdigit() and h.isdigit() and d.isdigit():
        if 1 <= int(d) <= 31:
            return "Monthly"
    # Every N days: M H */N * *
    if mo == "*" and dow == "*" and m.isdigit() and h.isdigit():
        if d.startswith("*/") and d[2:].isdigit() and int(d[2:]) >= 2:
            return "Every N days"
    return "Custom"


def _parse_fields(cron: str) -> dict[str, int]:
    out = {"minute": 0, "hour": 9, "dow": 0, "dom": 1, "every_n": 2}
    parts = (cron or "").strip().split()
    if len(parts) != 5:
        return out
    m, h, d, _mo, dow = parts
    if m.isdigit() and 0 <= int(m) <= 59:
        out["minute"] = int(m)
    if h.isdigit() and 0 <= int(h) <= 23:
        out["hour"] = int(h)
    if d.isdigit() and 1 <= int(d) <= 31:
        out["dom"] = int(d)
    if dow.isdigit() and 0 <= int(dow) <= 6:
        out["dow"] = int(dow)
    if d.startswith("*/") and d[2:].isdigit() and int(d[2:]) >= 2:
        out["every_n"] = int(d[2:])
    return out
