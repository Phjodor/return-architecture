"""Install-wide settings: API keys, defaults, port, log retention.

Secrets are never displayed back. Each provider row shows "set" / "not set"
and a Replace affordance that reveals a write-only paste field.
"""

from __future__ import annotations

import streamlit as st

from return_architecture import paths
from return_architecture.gui import helpers


PROVIDERS = ("anthropic", "openai", "gemini")


def render() -> None:
    st.title("Install settings")
    st.caption(f"Install root: `{paths.install_root()}`")

    _render_api_keys()
    st.divider()
    _render_general()


# ── API keys ──────────────────────────────────────────────────────────────

def _render_api_keys() -> None:
    st.subheader("API keys")
    st.write(
        "Provider keys are stored locally in your install secrets file and "
        "never shown after being set. To rotate a key, use **Replace**."
    )

    secrets = helpers.load_install_secrets_raw()

    for provider in PROVIDERS:
        is_set = helpers.provider_key_set(secrets, provider)
        status = "✓ set" if is_set else "✗ not set"

        st.markdown(f"**{provider.title()}**: {status}")
        _render_provider_row(provider, is_set, secrets)
        st.write("")


def _render_provider_row(provider: str, is_set: bool, secrets: dict) -> None:
    replace_flag = f"_replace_{provider}"

    if st.session_state.get(replace_flag, False):
        new_value = st.text_input(
            f"Paste your {provider.title()} API key",
            type="password",
            key=f"_input_{provider}",
        )
        cols = st.columns([1, 1, 6])
        if cols[0].button("Save", key=f"_save_{provider}"):
            if not new_value.strip():
                st.error("Empty value — paste a key first.")
            else:
                helpers.set_provider_key(secrets, provider, new_value)
                helpers.write_install_secrets(secrets)
                st.session_state[replace_flag] = False
                st.success(f"{provider.title()} key saved.")
                st.rerun()
        if cols[1].button("Cancel", key=f"_cancel_{provider}"):
            st.session_state[replace_flag] = False
            st.rerun()
    else:
        label = "Replace" if is_set else "Set"
        if st.button(label, key=f"_replace_btn_{provider}"):
            st.session_state[replace_flag] = True
            st.rerun()


# ── General ───────────────────────────────────────────────────────────────

def _render_general() -> None:
    st.subheader("General")

    config = helpers.load_install_config_raw()
    install_section = config.get("install", {}) or {}
    gui_section = config.get("gui", {}) or {}
    logs_section = config.get("logs", {}) or {}

    agents = helpers.list_agents()
    default_agent: str | None = None
    if agents:
        current = install_section.get("default_agent") or agents[0]
        if current not in agents:
            current = agents[0]
        default_agent = st.selectbox(
            "Default agent",
            options=agents,
            index=agents.index(current),
        )
    else:
        st.info("No agents yet — the default-agent setting becomes available once you create one.")

    with st.expander("Advanced", expanded=False):
        port = st.number_input(
            "GUI port — the localhost port this control panel binds to. "
            "Only change if another app is already using the default.",
            min_value=1024,
            max_value=65535,
            value=int(gui_section.get("port", 8501)),
            step=1,
        )
        retention = st.number_input(
            "Log retention (days) — raw conversation/tool/cost logs are "
            "deleted after this. Memory and tagged items are kept regardless.",
            min_value=1,
            max_value=3650,
            value=int(logs_section.get("retention_days", 90)),
            step=1,
        )

    if st.button("Save general settings"):
        config.setdefault("install", {})
        if default_agent is not None:
            config["install"]["default_agent"] = default_agent
        config.setdefault("gui", {})["port"] = int(port)
        config.setdefault("logs", {})["retention_days"] = int(retention)
        helpers.write_install_config(config)
        st.success("Saved.")
