"""Telegram page — bot token, chat_id, and connection tests."""

from __future__ import annotations

import asyncio

import streamlit as st

from return_architecture import telegram_worker
from return_architecture.gui import helpers


def render() -> None:
    slug = st.session_state.get("agent_slug")
    if not slug:
        st.warning("No agent selected. Use the sidebar.")
        return

    st.title(f"Telegram — {slug}")
    st.caption(
        "The agent reaches you through a Telegram bot you create with @BotFather. "
        "This page holds the bot token and your chat ID."
    )

    with st.expander("How to set this up if you haven't yet", expanded=False):
        st.markdown(
            "1. Open Telegram and search for **@BotFather**, then start a chat.\n"
            "2. Send `/newbot` and follow the prompts. Pick any name and a username "
            "   ending in `bot`.\n"
            "3. BotFather replies with a **bot token** that looks like "
            "   `123456789:ABC-DEF…`. Copy it.\n"
            "4. Open your new bot's chat in Telegram and send it any message (e.g. \"hi\").\n"
            "5. Paste the token below under **Bot token**, then click **Discover** "
            "   to auto-detect your chat ID.\n\n"
            "More background: [Telegram bot docs](https://core.telegram.org/bots#botfather)."
        )

    secrets = helpers.load_agent_secrets_raw(slug)
    tg = secrets.get("telegram") or {}
    token = (tg.get("bot_token") or "").strip()
    chat_id = str(tg.get("chat_id") or "").strip()

    st.divider()
    st.subheader("Bot token")
    _render_bot_token(slug, secrets, token)

    st.divider()
    st.subheader("Chat ID")
    _render_chat_id(slug, secrets, chat_id, has_token=bool(token))

    st.divider()
    st.subheader("Test connection")
    _render_test(slug, has_token=bool(token), has_chat_id=bool(chat_id))


# ── Bot token ─────────────────────────────────────────────────────────────

def _render_bot_token(slug: str, secrets: dict, current_token: str) -> None:
    st.markdown("Status: " + ("✓ **set**" if current_token else "✗ **not set**"))

    flag = f"_replace_tg_token_{slug}"

    if st.session_state.get(flag, False):
        new_value = st.text_input(
            "Paste your bot token",
            type="password",
            key=f"_tg_token_input_{slug}",
            placeholder="e.g. 8821667248:AAFEwI-fM5GnwB5…",
        )
        cols = st.columns([1, 1, 5])
        if cols[0].button("Save", key=f"_tg_token_save_{slug}"):
            if not new_value.strip():
                st.error("Empty value — paste a token first.")
            else:
                tg = secrets.setdefault("telegram", {})
                tg["bot_token"] = new_value.strip()
                helpers.write_agent_secrets(slug, secrets)
                st.session_state[flag] = False
                st.success("Bot token saved.")
                st.rerun()
        if cols[1].button("Cancel", key=f"_tg_token_cancel_{slug}"):
            st.session_state[flag] = False
            st.rerun()
    else:
        label = "Replace" if current_token else "Set"
        if st.button(label, key=f"_tg_token_replace_btn_{slug}"):
            st.session_state[flag] = True
            st.rerun()


# ── Chat ID ───────────────────────────────────────────────────────────────

def _render_chat_id(slug: str, secrets: dict, current_chat_id: str, *, has_token: bool) -> None:
    if current_chat_id:
        st.markdown(f"Current: **{current_chat_id}**")
    else:
        st.markdown("Status: ✗ **not set**")

    edit_flag = f"_edit_chat_id_{slug}"
    discovered_key = f"_discovered_chat_ids_{slug}"

    # Manual edit path
    if st.session_state.get(edit_flag, False):
        new_value = st.text_input(
            "Chat ID",
            value=current_chat_id,
            key=f"_chat_id_input_{slug}",
            placeholder="a numeric ID, e.g. 8781303996",
        )
        cols = st.columns([1, 1, 5])
        if cols[0].button("Save", key=f"_chat_id_save_{slug}"):
            cleaned = new_value.strip()
            if not cleaned.lstrip("-").isdigit():
                st.error("Chat ID must be numeric (may include a leading minus).")
            else:
                tg = secrets.setdefault("telegram", {})
                tg["chat_id"] = cleaned
                helpers.write_agent_secrets(slug, secrets)
                st.session_state[edit_flag] = False
                st.success("Chat ID saved.")
                st.rerun()
        if cols[1].button("Cancel", key=f"_chat_id_cancel_{slug}"):
            st.session_state[edit_flag] = False
            st.rerun()
        return

    # Buttons row
    btn_cols = st.columns([1, 1, 5])
    edit_label = "Replace" if current_chat_id else "Set manually"
    if btn_cols[0].button(edit_label, key=f"_chat_id_edit_btn_{slug}"):
        st.session_state[edit_flag] = True
        st.rerun()
    if btn_cols[1].button(
        "Discover",
        disabled=not has_token,
        help=(
            "Fetches recent bot updates. Make sure you've sent at least one "
            "message to the bot in Telegram first."
            if has_token
            else "Set a bot token first."
        ),
        key=f"_chat_id_discover_{slug}",
    ):
        try:
            with st.spinner("Fetching recent bot updates…"):
                ids = asyncio.run(telegram_worker.fetch_chat_ids(slug))
            st.session_state[discovered_key] = ids
        except Exception as e:
            st.error(f"Discover failed: {e}")

    # Show discovered IDs (persist via session_state)
    discovered = st.session_state.get(discovered_key)
    if discovered is not None:
        if not discovered:
            st.info(
                "No recent updates found. Send any message to your bot in Telegram, "
                "then click Discover again."
            )
        else:
            st.write(f"Found {len(discovered)} chat ID(s):")
            for cid, name in discovered:
                if st.button(
                    f"Use {cid}  ({name or '?'})",
                    key=f"_use_cid_{slug}_{cid}",
                ):
                    tg = secrets.setdefault("telegram", {})
                    tg["chat_id"] = str(cid)
                    helpers.write_agent_secrets(slug, secrets)
                    st.session_state.pop(discovered_key, None)
                    st.success(f"Chat ID set to {cid}.")
                    st.rerun()


# ── Test ──────────────────────────────────────────────────────────────────

def _render_test(slug: str, *, has_token: bool, has_chat_id: bool) -> None:
    cols = st.columns([1, 1, 5])
    if cols[0].button(
        "Test bot token",
        disabled=not has_token,
        help="Calls Telegram's getMe to verify the bot token is valid.",
        key=f"_tg_test_bot_{slug}",
    ):
        try:
            with st.spinner("Checking…"):
                username = asyncio.run(telegram_worker.test_bot_token(slug))
            st.success(f"✓ Bot reachable as @{username}.")
        except Exception as e:
            st.error(f"Bot check failed: {e}")

    if cols[1].button(
        "Send test message",
        disabled=not (has_token and has_chat_id),
        help=(
            "Sends a short test message to the configured chat ID. "
            "Check your Telegram to confirm it arrived."
            if has_token and has_chat_id
            else "Both bot token and chat ID need to be set."
        ),
        key=f"_tg_test_msg_{slug}",
    ):
        try:
            with st.spinner("Sending…"):
                asyncio.run(telegram_worker.send_test_message(slug))
            st.success("✓ Test message sent. Check your Telegram chat.")
        except Exception as e:
            st.error(f"Send failed: {e}")
