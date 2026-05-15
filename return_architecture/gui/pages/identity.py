"""Identity editor — display name, provider, model, behavior."""

from __future__ import annotations

import streamlit as st

from return_architecture import service as ra_service
from return_architecture.gui import helpers


PROVIDERS = ["anthropic", "openai"]

# Suggested models per provider — informational; the model field is free-text
# because new models ship frequently and we don't want the GUI to gatekeep.
MODEL_HINTS = {
    "anthropic": "e.g., claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5-20251001",
    "openai":    "e.g., gpt-5, gpt-4o (check OpenAI's current model list)",
}


def render() -> None:
    slug = st.session_state.get("agent_slug")
    if not slug:
        st.warning("No agent selected. Use the sidebar.")
        return

    st.title(f"Identity — {slug}")
    st.caption(
        "Who this agent is to the system. Changes take effect after the next "
        "service restart. The slug (folder name) cannot be changed here."
    )

    config = helpers.load_agent_config_raw(slug)
    agent_section = config.get("agent", {}) or {"slug": slug, "name": slug}
    model_section = config.get("model", {}) or {}
    behavior_section = config.get("behavior", {}) or {}

    st.subheader("Display")
    new_name = st.text_input(
        "Display name (used in UI; the slug stays the same)",
        value=agent_section.get("name", slug),
        key=f"_name_{slug}",
    )

    st.subheader("Model")
    current_provider = model_section.get("provider", "anthropic")
    if current_provider not in PROVIDERS:
        current_provider = "anthropic"
    new_provider = st.selectbox(
        "Provider",
        options=PROVIDERS,
        index=PROVIDERS.index(current_provider),
        key=f"_provider_{slug}",
    )
    new_model = st.text_input(
        "Model name",
        value=model_section.get("name", ""),
        help=MODEL_HINTS.get(new_provider, ""),
        key=f"_model_{slug}",
    )
    new_max_tokens = st.number_input(
        "Max tokens per turn",
        min_value=64, max_value=200_000,
        value=int(model_section.get("max_tokens", 4096)),
        step=256,
        key=f"_maxtok_{slug}",
    )

    st.markdown("**Temperature**")
    st.caption(
        "Controls how varied the model's output is. Lower values (around 0 – 0.5) "
        "make responses more deterministic and focused; higher values "
        "(0.8 – 1.5) make them more varied and exploratory. Most models default "
        "to 1.0. "
        "**Not every model supports this setting** — some (notably OpenAI's "
        "reasoning models and certain gpt-5 variants) only accept the default "
        "and will error if you set it explicitly. If unsure, leave it off and "
        "use **Test connection** below to verify."
    )

    has_temperature = "temperature" in model_section and model_section.get("temperature") is not None
    use_temperature = st.checkbox(
        "Set temperature explicitly",
        value=has_temperature,
        key=f"_use_temp_{slug}",
    )
    if use_temperature:
        new_temperature = st.number_input(
            "Temperature value",
            min_value=0.0, max_value=2.0,
            value=float(model_section.get("temperature") or 1.0),
            step=0.1,
            format="%.2f",
            key=f"_temp_{slug}",
            label_visibility="collapsed",
        )
    else:
        new_temperature = None

    st.subheader("Behavior")
    new_silence = st.checkbox(
        "Allow silence (bias the system prompt toward the no_response tool)",
        value=bool(behavior_section.get("silence_allowed", True)),
        key=f"_silence_{slug}",
    )
    new_self_sched = st.number_input(
        "Max self-scheduled jobs per day (cap on how many times the agent can wake itself)",
        min_value=0, max_value=100,
        value=int(behavior_section.get("max_self_scheduled_jobs_per_day", 5)),
        step=1,
        key=f"_selfsched_{slug}",
    )

    st.divider()

    def _apply_to_config() -> dict:
        config.setdefault("agent", {})["slug"] = slug
        config["agent"]["name"] = new_name.strip() or slug
        config.setdefault("model", {})
        config["model"]["provider"] = new_provider
        config["model"]["name"] = new_model.strip()
        config["model"]["max_tokens"] = int(new_max_tokens)
        if new_temperature is None:
            config["model"].pop("temperature", None)
        else:
            config["model"]["temperature"] = float(new_temperature)
        config.setdefault("behavior", {})
        config["behavior"]["silence_allowed"] = bool(new_silence)
        config["behavior"]["max_self_scheduled_jobs_per_day"] = int(new_self_sched)
        return config

    if st.button("Test connection", key="_test_identity"):
        _run_connection_test(
            provider=new_provider,
            model=new_model.strip(),
            temperature=new_temperature,
        )

    st.divider()
    save_cols = st.columns([1, 2, 5])
    if save_cols[0].button("Save", key="_save_identity"):
        if not new_model.strip():
            st.error("Model name is required.")
        else:
            helpers.write_agent_config(slug, _apply_to_config())
            st.success("Saved. Restart the service for changes to take effect.")

    if save_cols[1].button("Save & reload service", key="_save_reload_identity"):
        st.session_state["_confirm_reload_identity"] = True

    if st.session_state.get("_confirm_reload_identity"):
        st.warning(
            "Reloading the service will drop the in-memory conversation "
            "thread. Long-term memory (Chroma) is preserved. Continue?"
        )
        confirm_cols = st.columns([1, 1, 6])
        if confirm_cols[0].button("Yes, reload", key="_confirm_identity_yes"):
            if not new_model.strip():
                st.error("Model name is required.")
            else:
                helpers.write_agent_config(slug, _apply_to_config())
                try:
                    with st.spinner("Restarting service…"):
                        ra_service.restart(slug)
                    st.success("Saved and restarted.")
                except (RuntimeError, FileNotFoundError) as e:
                    st.error(f"Restart failed: {e}")
                st.session_state["_confirm_reload_identity"] = False
                st.rerun()
        if confirm_cols[1].button("Cancel", key="_confirm_identity_no"):
            st.session_state["_confirm_reload_identity"] = False
            st.rerun()


def _run_connection_test(*, provider: str, model: str, temperature: float | None) -> None:
    """Send a tiny test request and report whether it succeeds.

    Tests the *unsaved* form values so you can iterate without committing
    a broken config.
    """
    from return_architecture.providers import Message
    from return_architecture.providers.anthropic_provider import AnthropicProvider
    from return_architecture.providers.openai_provider import OpenAIProvider

    if not model:
        st.error("Set a model name first.")
        return

    secrets = helpers.load_install_secrets_raw()
    key = (secrets.get("providers") or {}).get(provider, "") or ""
    if not key:
        st.error(
            f"No {provider} API key set. Add one in **Install settings** before testing."
        )
        return

    try:
        if provider == "anthropic":
            client = AnthropicProvider(api_key=key)
        elif provider == "openai":
            client = OpenAIProvider(api_key=key)
        else:
            st.error(f"Unknown provider: {provider}")
            return

        with st.spinner("Testing…"):
            resp = client.complete(
                system="Reply with a single word.",
                messages=[Message(role="user", content="say ok")],
                tools=[],
                model=model,
                max_tokens=20,
                temperature=temperature,
            )
        text = (resp.text or "").strip()
        if text:
            st.success(f"✓ Works. Model replied: {text[:200]}")
        else:
            st.success("✓ Works (no text returned, but the call succeeded).")
    except Exception as e:
        st.error(f"Connection test failed:\n\n{e}")
