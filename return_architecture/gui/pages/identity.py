"""Identity editor — display name, provider, model, behavior."""

from __future__ import annotations

import streamlit as st

from return_architecture import service as ra_service
from return_architecture.gui import helpers


PROVIDERS = ["anthropic", "openai", "gemini"]

# Suggested models per provider — informational; the model field is free-text
# because new models ship frequently and we don't want the GUI to gatekeep.
MODEL_HINTS = {
    "anthropic": "e.g., claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5-20251001",
    "openai":    "e.g., gpt-5, gpt-4o (check OpenAI's current model list)",
    "gemini":    "e.g., gemini-2.5-pro, gemini-2.5-flash (check Google's current model list)",
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
    if new_provider in ("anthropic", "gemini"):
        new_max_tokens = st.number_input(
            "Max tokens per turn",
            min_value=64, max_value=200_000,
            value=int(model_section.get("max_tokens", 4096)),
            step=256,
            key=f"_maxtok_{slug}",
            help=(
                "Per-turn output cap. 4096 is a sensible default for chat. "
                "For Gemini 2.5 with thinking enabled, give yourself headroom — "
                "thinking tokens count against this budget."
            ),
        )
    else:
        new_max_tokens = int(model_section.get("max_tokens", 4096))
        st.caption(
            "OpenAI uses its own per-model output defaults — no cap is set from "
            "here. See [docs/configuration.md](https://github.com/Theapolar/return-architecture/blob/main/docs/configuration.md)."
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

    new_top_p, new_top_k, new_thinking_budget = _render_sampling_section(
        slug, new_provider, model_section
    )

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
    new_seed_n = st.number_input(
        "Seed chat history from memory — how many of the most recent past turns "
        "to pre-load as chat history at the start of each session. 0 = empty session "
        "(default; rely on semantic recall only). 30 = the agent 'arrives' with "
        "his last ~30 turns already in context.",
        min_value=0, max_value=500,
        value=int(behavior_section.get("seed_chat_history_from_memory", 0)),
        step=10,
        key=f"_seedmem_{slug}",
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
        if new_top_p is None:
            config["model"].pop("top_p", None)
        else:
            config["model"]["top_p"] = float(new_top_p)
        if new_top_k is None:
            config["model"].pop("top_k", None)
        else:
            config["model"]["top_k"] = int(new_top_k)
        if new_thinking_budget is None:
            config["model"].pop("thinking_budget", None)
        else:
            config["model"]["thinking_budget"] = int(new_thinking_budget)
        config.setdefault("behavior", {})
        config["behavior"]["silence_allowed"] = bool(new_silence)
        config["behavior"]["max_self_scheduled_jobs_per_day"] = int(new_self_sched)
        config["behavior"]["seed_chat_history_from_memory"] = int(new_seed_n)
        return config

    if st.button("Test connection", key="_test_identity"):
        _run_connection_test(
            provider=new_provider,
            model=new_model.strip(),
            temperature=new_temperature,
            top_p=new_top_p,
            top_k=new_top_k,
            thinking_budget=new_thinking_budget,
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


def _render_sampling_section(
    slug: str, provider: str, model_section: dict
) -> tuple[float | None, int | None, int | None]:
    """Render sampling knobs (top_p, top_k) and, for Gemini, thinking budget.
    Returns (top_p, top_k, thinking_budget) — each None when not explicitly set.
    """
    with st.expander("Sampling & reasoning (optional)", expanded=False):
        st.caption(
            "All three knobs default to the provider's own setting. Only check a "
            "box if you want to override."
        )

        # top_p — supported by all three providers.
        has_top_p = model_section.get("top_p") is not None
        use_top_p = st.checkbox(
            "Set top_p (nucleus sampling — keep the smallest set of tokens whose "
            "cumulative probability exceeds this value)",
            value=has_top_p,
            key=f"_use_topp_{slug}",
        )
        new_top_p: float | None
        if use_top_p:
            new_top_p = float(st.number_input(
                "top_p value",
                min_value=0.0, max_value=1.0,
                value=float(model_section.get("top_p") or 0.95),
                step=0.01, format="%.2f",
                key=f"_topp_{slug}",
                label_visibility="collapsed",
            ))
        else:
            new_top_p = None

        # top_k — anthropic + gemini only.
        new_top_k: int | None
        if provider in ("anthropic", "gemini"):
            has_top_k = model_section.get("top_k") is not None
            use_top_k = st.checkbox(
                "Set top_k (only sample from the k highest-probability tokens)",
                value=has_top_k,
                key=f"_use_topk_{slug}",
            )
            if use_top_k:
                new_top_k = int(st.number_input(
                    "top_k value",
                    min_value=1, max_value=2000,
                    value=int(model_section.get("top_k") or 40),
                    step=1,
                    key=f"_topk_{slug}",
                    label_visibility="collapsed",
                ))
            else:
                new_top_k = None
        else:
            new_top_k = None
            st.caption("top_k is not supported by OpenAI — leave unset.")

        # thinking_budget — gemini only.
        new_thinking_budget: int | None
        if provider == "gemini":
            st.markdown("**Thinking budget** (Gemini-only)")
            st.caption(
                "Controls how many tokens Gemini 2.5 spends reasoning before "
                "replying. **-1** lets the model decide dynamically (the default "
                "for 2.5 Pro and Flash). **0** disables thinking entirely on "
                "2.5 Flash (cheaper, faster, less capable). A **positive number** "
                "is a hard cap. Thinking tokens count against your Max tokens — "
                "give yourself headroom."
            )
            has_tb = model_section.get("thinking_budget") is not None
            use_tb = st.checkbox(
                "Set thinking budget explicitly",
                value=has_tb,
                key=f"_use_tb_{slug}",
            )
            if use_tb:
                new_thinking_budget = int(st.number_input(
                    "Thinking budget value (-1 = dynamic, 0 = disabled, >0 = cap)",
                    min_value=-1, max_value=32768,
                    value=int(model_section.get("thinking_budget") if has_tb else -1),
                    step=128,
                    key=f"_tb_{slug}",
                ))
            else:
                new_thinking_budget = None
        else:
            new_thinking_budget = None

    return new_top_p, new_top_k, new_thinking_budget


def _run_connection_test(
    *,
    provider: str,
    model: str,
    temperature: float | None,
    top_p: float | None = None,
    top_k: int | None = None,
    thinking_budget: int | None = None,
) -> None:
    """Send a tiny test request and report whether it succeeds.

    Tests the *unsaved* form values so you can iterate without committing
    a broken config.
    """
    from return_architecture.providers import Message
    from return_architecture.providers.anthropic_provider import AnthropicProvider
    from return_architecture.providers.gemini_provider import GeminiProvider
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
        elif provider == "gemini":
            client = GeminiProvider(api_key=key)
        else:
            st.error(f"Unknown provider: {provider}")
            return

        with st.spinner("Testing…"):
            resp = client.complete(
                system="Reply with a single word.",
                messages=[Message(role="user", content="say ok")],
                tools=[],
                model=model,
                max_tokens=2048 if provider == "gemini" else 20,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                thinking_budget=thinking_budget,
            )
        text = (resp.text or "").strip()
        if text:
            st.success(f"✓ Works. Model replied: {text[:200]}")
        else:
            st.success("✓ Works (no text returned, but the call succeeded).")
    except Exception as e:
        st.error(f"Connection test failed:\n\n{e}")
