"""Service page — control the background daemon (launchd on macOS, systemd user units on Linux)."""

from __future__ import annotations

import streamlit as st

from return_architecture import service as ra_service


def render() -> None:
    slug = st.session_state.get("agent_slug")
    if not slug:
        st.warning("No agent selected. Use the sidebar.")
        return

    st.title(f"Service — {slug}")
    st.caption(
        "The background daemon that runs Telegram + scheduler for this agent. "
        "When installed, it auto-starts at login and respawns on crash."
    )

    try:
        status = ra_service.status(slug)
    except RuntimeError as e:
        st.info(str(e))
        return

    _render_status(status)
    st.divider()
    _render_actions(slug, status)
    st.divider()
    _render_logs(slug)


# ── Status ────────────────────────────────────────────────────────────────

def _render_status(status: ra_service.ServiceStatus) -> None:
    st.subheader("Status")

    cols = st.columns(2)
    with cols[0]:
        if status.loaded:
            pid_part = f" (PID {status.pid})" if status.pid else ""
            st.success(f"loaded ✓{pid_part}")
        else:
            st.warning("not loaded")
        st.markdown(f"**Label**: `{status.label}`")
    with cols[1]:
        st.markdown(f"**Service file**: `{status.service_file_path}`")
        st.markdown(f"**File**: {'exists' if status.service_file_exists else 'missing'}")


# ── Actions ───────────────────────────────────────────────────────────────

def _render_actions(slug: str, status: ra_service.ServiceStatus) -> None:
    st.subheader("Actions")

    cols = st.columns([1, 1, 1, 4])

    if cols[0].button(
        "Install",
        disabled=status.loaded,
        help=(
            "Already loaded — use Restart to apply config changes."
            if status.loaded
            else "Write the service file and start the daemon."
        ),
        key="_svc_install",
    ):
        try:
            with st.spinner("Installing…"):
                ra_service.install(slug)
            st.success("Service installed and running.")
            st.rerun()
        except (RuntimeError, FileNotFoundError) as e:
            st.error(f"Install failed: {e}")

    if cols[1].button(
        "Restart",
        disabled=not status.loaded,
        help=(
            "Terminates the running daemon process; the service manager "
            "respawns it and the new daemon re-reads your config."
            if status.loaded
            else "Service is not loaded — use Install."
        ),
        key="_svc_restart",
    ):
        try:
            with st.spinner("Restarting…"):
                ra_service.restart(slug)
            st.success("Service restarted.")
            st.rerun()
        except (RuntimeError, FileNotFoundError) as e:
            st.error(f"Restart failed: {e}")

    can_uninstall = status.loaded or status.service_file_exists
    if cols[2].button(
        "Uninstall",
        disabled=not can_uninstall,
        help=(
            "Stops the daemon and removes the service file. Your config, memory, "
            "letters and items are NOT affected; reinstall any time."
            if can_uninstall
            else "Nothing to uninstall."
        ),
        key="_svc_uninstall_btn",
    ):
        st.session_state["_confirm_uninstall"] = True
        st.rerun()

    if st.session_state.get("_confirm_uninstall"):
        st.warning(
            f"Uninstall the **{slug}** service? The daemon will stop running and "
            "the service file will be removed. Your config, memory, items, letters, and "
            "artifacts are NOT affected — you can reinstall any time."
        )
        confirm_cols = st.columns([1, 1, 5])
        if confirm_cols[0].button("Yes, uninstall", key="_confirm_uninstall_yes"):
            try:
                with st.spinner("Uninstalling…"):
                    ra_service.uninstall(slug)
                st.success("Service uninstalled.")
            except RuntimeError as e:
                st.error(f"Uninstall failed: {e}")
            st.session_state["_confirm_uninstall"] = False
            st.rerun()
        if confirm_cols[1].button("Cancel", key="_confirm_uninstall_no"):
            st.session_state["_confirm_uninstall"] = False
            st.rerun()


# ── Logs ──────────────────────────────────────────────────────────────────

def _render_logs(slug: str) -> None:
    st.subheader("Recent output")
    st.caption(
        "Last N lines from the daemon's stdout and stderr log files. "
        "Stdout includes startup messages and scheduler pings; stderr "
        "shows errors and tracebacks."
    )

    cols = st.columns([1, 1, 5])
    lines = cols[0].number_input(
        "Lines per log",
        min_value=10, max_value=500,
        value=40, step=10,
        key="_logs_lines",
        label_visibility="collapsed",
    )
    cols[0].caption("lines per log")
    if cols[1].button("Refresh", key="_logs_refresh"):
        st.rerun()

    try:
        stdout, stderr = ra_service.tail_logs(slug, lines=int(lines))
    except RuntimeError as e:
        st.info(str(e))
        return

    st.markdown("**stdout**")
    if stdout:
        st.code(stdout, language="text")
    else:
        st.caption("(empty)")

    st.markdown("**stderr**")
    if stderr:
        st.code(stderr, language="text")
    else:
        st.caption("(empty)")
