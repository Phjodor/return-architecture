"""System prompt editor — the agent's identity in text form."""

from __future__ import annotations

import streamlit as st

from return_architecture import service as ra_service
from return_architecture.gui import helpers


def render() -> None:
    slug = st.session_state.get("agent_slug")
    if not slug:
        st.warning("No agent selected. Use the sidebar.")
        return

    st.title(f"System prompt — {slug}")
    st.caption(
        "This text is the agent's identity. It loads at the start of every "
        "session. Edits here take effect after the next service restart."
    )

    current = helpers.load_system_prompt(slug)

    new_value = st.text_area(
        "System prompt",
        value=current,
        height=400,
        key=f"_prompt_text_{slug}",
        label_visibility="collapsed",
    )

    save_cols = st.columns([1, 2, 5])
    if save_cols[0].button("Save", key="_save_prompt"):
        helpers.write_system_prompt(slug, new_value)
        st.success("Saved. The new prompt will apply next time the service restarts.")

    if save_cols[1].button("Save & reload service", key="_save_reload_prompt"):
        st.session_state["_confirm_reload_prompt"] = True

    if st.session_state.get("_confirm_reload_prompt"):
        st.warning(
            "Reloading the service will drop the in-memory conversation "
            "thread. Long-term memory (Chroma) is preserved. Continue?"
        )
        confirm_cols = st.columns([1, 1, 6])
        if confirm_cols[0].button("Yes, reload", key="_confirm_yes"):
            helpers.write_system_prompt(slug, new_value)
            try:
                with st.spinner("Restarting service…"):
                    ra_service.restart(slug)
                st.success("Saved and restarted.")
            except (RuntimeError, FileNotFoundError) as e:
                st.error(f"Restart failed: {e}")
            st.session_state["_confirm_reload_prompt"] = False
            st.rerun()
        if confirm_cols[1].button("Cancel", key="_confirm_no"):
            st.session_state["_confirm_reload_prompt"] = False
            st.rerun()
