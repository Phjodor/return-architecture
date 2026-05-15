"""Overview — per-agent status, identity, and counts."""

from __future__ import annotations

import streamlit as st

from return_architecture import items as ra_items
from return_architecture import service as ra_service
from return_architecture.gui import helpers


def render() -> None:
    slug = st.session_state.get("agent_slug")
    if not slug:
        st.warning("No agent selected. Use the sidebar.")
        return

    config = helpers.load_agent_config_raw(slug)
    agent_section = config.get("agent", {}) or {}
    model_section = config.get("model", {}) or {}
    behavior_section = config.get("behavior", {}) or {}

    st.title(agent_section.get("name") or slug)
    st.caption(f"Slug: `{slug}`")

    cols = st.columns(2)

    with cols[0]:
        st.subheader("Identity")
        st.write(f"**Provider**: {model_section.get('provider', '—')}")
        st.write(f"**Model**: {model_section.get('name', '—')}")
        st.write(f"**Max tokens per turn**: {model_section.get('max_tokens', '—')}")
        silence = behavior_section.get("silence_allowed", True)
        st.write(f"**Silence allowed**: {'yes' if silence else 'no'}")

    with cols[1]:
        st.subheader("Service")
        try:
            status = ra_service.status(slug)
            if status.loaded:
                pid_part = f" (PID {status.pid})" if status.pid else ""
                st.success(f"loaded ✓{pid_part}")
            else:
                st.warning("not loaded")
        except RuntimeError as e:
            st.info(str(e))

        last_iso = helpers.last_log_timestamp(slug)
        if last_iso:
            short = last_iso[:19].replace("T", " ")
            st.write(f"**Last activity**: {short} UTC")
        else:
            st.write("**Last activity**: (none yet)")

    st.divider()

    # Tagged-item counts
    st.subheader("Open items")
    try:
        counts = ra_items.count_by_kind(slug)
    except Exception:
        counts = {}
    cols = st.columns(4)
    for i, kind in enumerate(("note", "important", "question", "commitment")):
        cols[i].metric(f"{kind}s", counts.get(kind, 0))

    # Outbox / inbox counts
    st.subheader("Letters & inbox")
    cols = st.columns(2)
    cols[0].metric("letters in outbox", len(helpers.list_outbox(slug)))
    cols[1].metric("files in inbox", len(helpers.list_inbox(slug)))
