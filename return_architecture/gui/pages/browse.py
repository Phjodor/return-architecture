"""Browse — letters, inbox, items, and question responses."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from return_architecture import items as ra_items
from return_architecture import question_sessions as ra_qs
from return_architecture.gui import helpers


def render() -> None:
    slug = st.session_state.get("agent_slug")
    if not slug:
        st.warning("No agent selected. Use the sidebar.")
        return

    st.title(f"Browse — {slug}")

    letters_tab, inbox_tab, items_tab, responses_tab = st.tabs([
        "Letters", "Inbox", "Items", "Question responses",
    ])

    with letters_tab:
        _render_letters(slug)
    with inbox_tab:
        _render_inbox(slug)
    with items_tab:
        _render_items(slug)
    with responses_tab:
        _render_responses(slug)


# ── Letters ───────────────────────────────────────────────────────────────

def _read_title(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
        return first.lstrip("# ").strip() or path.stem
    except OSError:
        return path.stem


def _render_letters(slug: str) -> None:
    st.caption(
        "Letters the agent has written for you via the `write_letter` tool. "
        "These live in `<agent>/outbox/` and can also be read on Telegram with `/letters`."
    )

    files = helpers.list_outbox(slug)
    files.sort(key=lambda p: p.name, reverse=True)

    if not files:
        st.info("No letters yet.")
        return

    for path in files:
        title = _read_title(path)
        with st.expander(f"📜 {title}  ·  {path.name}", expanded=False):
            try:
                body = path.read_text(encoding="utf-8")
            except OSError as e:
                st.error(f"Could not read: {e}")
                continue
            st.markdown(body)

            del_flag = f"_del_letter_{path.name}"
            if st.button("Delete", key=f"_del_letter_btn_{path.name}"):
                st.session_state[del_flag] = True
                st.rerun()
            if st.session_state.get(del_flag):
                st.warning(f"Delete `{path.name}`? This can't be undone.")
                cols = st.columns([1, 1, 5])
                if cols[0].button("Yes, delete", key=f"_del_letter_yes_{path.name}"):
                    try:
                        path.unlink()
                    except OSError as e:
                        st.error(f"Delete failed: {e}")
                    else:
                        st.session_state[del_flag] = False
                        st.success("Deleted.")
                        st.rerun()
                if cols[1].button("Cancel", key=f"_del_letter_no_{path.name}"):
                    st.session_state[del_flag] = False
                    st.rerun()


# ── Inbox ─────────────────────────────────────────────────────────────────

def _render_inbox(slug: str) -> None:
    st.caption(
        "Files you've placed in `<agent>/inbox/` for the agent to read at its own pace. "
        "The agent picks them up via the Telegram `/inbox` command or during scheduled pings — "
        "they don't interrupt automatically."
    )

    files = helpers.list_inbox(slug)
    files.sort(key=lambda p: p.name, reverse=True)

    if not files:
        st.info("Inbox is empty. Drop a text file in there for the agent to read.")
        return

    for path in files:
        with st.expander(f"📥 {path.name}", expanded=False):
            try:
                body = path.read_text(encoding="utf-8")
            except OSError as e:
                st.error(f"Could not read: {e}")
                continue
            preview = body if len(body) <= 10_000 else body[:10_000]
            st.text(preview)
            if len(body) > 10_000:
                st.caption(f"(showing first 10,000 of {len(body)} characters)")


# ── Items ─────────────────────────────────────────────────────────────────

def _render_items(slug: str) -> None:
    st.caption(
        "Tagged items: notes, important moments, open questions, commitments. "
        "Created by you via Telegram hashtags or by the agent via the `tag_item` tool."
    )

    cols = st.columns([2, 2, 5])
    kind_choice = cols[0].selectbox(
        "Kind",
        options=["all", *ra_items.KINDS],
        index=0,
        key="_items_kind_filter",
    )
    status_choice = cols[1].selectbox(
        "Status",
        options=["open", "resolved", "all"],
        index=0,
        key="_items_status_filter",
    )

    rows = ra_items.list_items(
        slug,
        kind=None if kind_choice == "all" else kind_choice,
        status=None if status_choice == "all" else status_choice,
        limit=200,
    )

    if not rows:
        st.info("No items match these filters.")
        return

    for item in rows:
        date = item.created_at[:10] if item.created_at else "?"
        preview = item.body[:80].replace("\n", " ")
        label = f"[{date}] ({item.kind}, by {item.source})  #{item.id}  {preview}"
        with st.expander(label, expanded=False):
            st.write(item.body)
            st.caption(
                f"created: {item.created_at}  ·  "
                f"status: {item.status}  ·  "
                f"source_ref: {item.source_ref or '—'}"
            )
            if item.status == "open":
                if st.button("Mark resolved", key=f"_resolve_item_{item.id}"):
                    ra_items.resolve_item(slug, item.id)
                    st.success("Resolved.")
                    st.rerun()


# ── Question responses ────────────────────────────────────────────────────

def _render_responses(slug: str) -> None:
    st.caption(
        "Q&A from the agent's scheduled question sessions, grouped by session."
    )

    grouped = ra_qs.get_all_responses_grouped(slug)
    if not grouped:
        st.info(
            "No question session responses yet. Enable the `question_session` "
            "schedule (under Schedules) to start collecting these."
        )
        return

    for session_id in sorted(grouped.keys(), reverse=True):
        rows = grouped[session_id]
        answered = sum(1 for r in rows if not r.get("skipped"))
        skipped = len(rows) - answered
        label = f"📋 {session_id}  ·  {answered} answered, {skipped} skipped"
        with st.expander(label, expanded=False):
            for r in rows:
                question = r.get("question", "")
                answer = (r.get("response") or "").strip()
                qtype = r.get("question_type", "?")
                if r.get("skipped"):
                    st.markdown(f"**Q** ({qtype}) — *{question}*")
                    st.caption("(skipped)")
                else:
                    st.markdown(f"**Q** ({qtype}) — *{question}*")
                    if answer:
                        st.write(answer)
                    else:
                        st.caption("(empty answer)")
                st.write("")
