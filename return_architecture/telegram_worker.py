"""Telegram channel for an agent.

The worker polls Telegram for messages from the configured human (matched
by chat_id), runs runtime.turn() on each message, and sends the reply
back. Silence (no_response from the agent) results in no Telegram message
being sent — the channel honours the agent's choice.

This is a transport/channel, not a tool. Agent-initiated messages come
via the send_to_human_telegram tool, typically from scheduled pings.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import tomllib

from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from return_architecture import artifact_exchange as ra_artifact
from return_architecture import items as ra_items
from return_architecture import logging as ralog
from return_architecture import paths, runtime
from return_architecture.providers import ImageContent


def _read_agent_secrets(slug: str) -> dict:
    path = paths.agent_secrets_path(slug)
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_telegram_creds(slug: str) -> tuple[str, int | None]:
    secrets = _read_agent_secrets(slug)
    tg = secrets.get("telegram", {})
    token = tg.get("bot_token", "")
    chat_id_raw = tg.get("chat_id", "")
    chat_id = int(chat_id_raw) if chat_id_raw not in ("", None) else None
    return token, chat_id


def build_application(
    slug: str,
    session: runtime.AgentSession,
    turn_lock: asyncio.Lock,
) -> Application:
    """Build the Telegram Application bound to a given agent session."""
    token, expected_chat_id = get_telegram_creds(slug)
    if not token:
        raise ValueError(
            f"No telegram bot_token in {paths.agent_secrets_path(slug)}. "
            f"Set it under [telegram] and try again."
        )
    if expected_chat_id is None:
        raise ValueError(
            f"No telegram chat_id in {paths.agent_secrets_path(slug)}. "
            f"Run `return-architecture telegram-discover {slug}` first."
        )

    app: Application = ApplicationBuilder().token(token).build()
    app.bot_data["session"] = session
    app.bot_data["slug"] = slug
    app.bot_data["expected_chat_id"] = expected_chat_id
    app.bot_data["turn_lock"] = turn_lock

    app.add_handler(CommandHandler("artifact", _on_artifact))
    app.add_handler(CommandHandler("letters", _on_letters))
    app.add_handler(CommandHandler("letter", _on_letter))
    app.add_handler(CommandHandler("notes", _on_items_command("note")))
    app.add_handler(CommandHandler("questions", _on_items_command("question")))
    app.add_handler(CommandHandler("important", _on_items_command("important")))
    app.add_handler(CommandHandler("commitments", _on_items_command("commitment")))
    app.add_handler(MessageHandler(filters.PHOTO, _on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))

    # Register commands with Telegram so they show in the chat's command menu.
    app.post_init = set_bot_commands
    return app


BOT_COMMANDS = [
    BotCommand("artifact", "Run an artifact exchange (files from artifacts/incoming/)"),
    BotCommand("letters", "List letters the agent has written to you"),
    BotCommand("letter", "Read a letter: /letter <N> (N from /letters)"),
    BotCommand("notes", "List open notes"),
    BotCommand("questions", "List open questions"),
    BotCommand("important", "List open important items"),
    BotCommand("commitments", "List open commitments"),
]


async def set_bot_commands(app: Application) -> None:
    """Register the bot's command menu with Telegram."""
    await app.bot.set_my_commands(BOT_COMMANDS)


def run_worker(slug: str) -> None:
    """Run the Telegram worker alone (no scheduler). Blocking."""
    session = runtime.build_session(slug)
    turn_lock = asyncio.Lock()
    app = build_application(slug, session, turn_lock)

    async def _post_shutdown(_app):
        session.close()
    app.post_shutdown = _post_shutdown

    print(f"[telegram] worker started for agent '{slug}'. Ctrl-C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


async def _keep_typing(bot, chat_id: int) -> None:
    """Keep Telegram's "typing…" action alive while the agent composes.

    Telegram clears the indicator after a few seconds, so we re-send it on an
    interval until the surrounding turn finishes and cancels this task. The
    indicator is a nicety, never a reason to fail a turn, so send errors are
    swallowed.
    """
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    incoming_chat_id = update.message.chat_id
    expected = context.bot_data.get("expected_chat_id")
    if expected is not None and incoming_chat_id != expected:
        ralog.log_event(context.bot_data["slug"], "telegram_message_rejected", {
            "from_chat_id": incoming_chat_id,
        })
        return

    text = update.message.text.strip()
    slug = context.bot_data["slug"]
    session = context.bot_data["session"]
    turn_lock: asyncio.Lock = context.bot_data["turn_lock"]

    ralog.log_event(slug, "telegram_message_in", {
        "from_chat_id": incoming_chat_id,
        "text": text,
    })

    # Hashtag tagging: store an item per kind found, body without the tags.
    tagged_kinds = ra_items.parse_tags(text)
    if tagged_kinds:
        body = ra_items.strip_tags(text)
        if body:
            for kind in tagged_kinds:
                try:
                    item_id = ra_items.add_item(
                        slug, kind=kind, body=body, source="human",
                        source_ref=f"telegram:{update.message.message_id}",
                    )
                    ralog.log_event(slug, "item_tagged", {
                        "kind": kind, "id": item_id, "source": "human",
                    })
                except ValueError:
                    pass

    typing_task = asyncio.create_task(_keep_typing(context.bot, incoming_chat_id))
    try:
        async with turn_lock:
            reply = await asyncio.to_thread(runtime.turn, session, text)
    except Exception as e:
        ralog.log_event(slug, "telegram_turn_error", {"error": repr(e)})
        await update.message.reply_text(f"[error: {e}]")
        return
    finally:
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task

    if reply:
        await _send_chunked(update, reply)
        ralog.log_event(slug, "telegram_message_out", {"text": reply})
    else:
        ralog.log_event(slug, "telegram_silence", {})


async def _on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return
    incoming_chat_id = update.message.chat_id
    expected = context.bot_data.get("expected_chat_id")
    if expected is not None and incoming_chat_id != expected:
        ralog.log_event(context.bot_data["slug"], "telegram_photo_rejected", {
            "from_chat_id": incoming_chat_id,
        })
        return

    slug = context.bot_data["slug"]
    session = context.bot_data["session"]
    turn_lock: asyncio.Lock = context.bot_data["turn_lock"]
    caption = (update.message.caption or "").strip()

    # Pick the highest-resolution available photo.
    photo = update.message.photo[-1]
    try:
        file = await context.bot.get_file(photo.file_id)
        raw = await file.download_as_bytearray()
    except Exception as e:
        ralog.log_event(slug, "telegram_photo_download_failed", {"error": repr(e)})
        await update.message.reply_text(f"[couldn't download the image: {e}]")
        return

    b64 = base64.b64encode(bytes(raw)).decode()
    # Telegram serves photos as JPEG. PNG is not delivered as a photo (it
    # arrives as a document). For document image support, we'd need a
    # separate handler; for now, JPEG is the common case.
    image = ImageContent(base64_data=b64, mime_type="image/jpeg")

    ralog.log_event(slug, "telegram_photo_in", {
        "from_chat_id": incoming_chat_id,
        "bytes": len(raw),
        "caption_chars": len(caption),
    })

    user_text = caption or "(the human sent an image without a caption)"
    typing_task = asyncio.create_task(_keep_typing(context.bot, incoming_chat_id))
    try:
        async with turn_lock:
            reply = await asyncio.to_thread(runtime.turn, session, user_text, [image])
    except Exception as e:
        ralog.log_event(slug, "telegram_photo_turn_error", {"error": repr(e)})
        await update.message.reply_text(f"[error: {e}]")
        return
    finally:
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task

    if reply:
        await _send_chunked(update, reply)
        ralog.log_event(slug, "telegram_message_out", {"text": reply})
    else:
        ralog.log_event(slug, "telegram_silence", {})


async def _on_artifact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    incoming_chat_id = update.message.chat_id
    expected = context.bot_data.get("expected_chat_id")
    if expected is not None and incoming_chat_id != expected:
        ralog.log_event(context.bot_data["slug"], "telegram_artifact_rejected", {
            "from_chat_id": incoming_chat_id,
        })
        return

    slug = context.bot_data["slug"]
    turn_lock: asyncio.Lock = context.bot_data["turn_lock"]

    ralog.log_event(slug, "telegram_artifact_triggered", {})
    await update.message.reply_text(
        "Starting artifact exchange — this takes about a minute. I'll send the signal when it's done."
    )

    try:
        async with turn_lock:
            result = await asyncio.to_thread(ra_artifact.run_exchange, slug)
    except (FileNotFoundError, ValueError) as e:
        await update.message.reply_text(f"Couldn't run the exchange: {e}")
        return
    except Exception as e:
        ralog.log_event(slug, "telegram_artifact_error", {"error": repr(e)})
        await update.message.reply_text(f"[error during exchange: {e}]")
        return

    message = (
        f"{result.for_human}\n\n"
        f"---\n"
        f"Exchange id: {result.exchange_id}\n"
        f"Files: {result.exchange_dir}"
    )
    await _send_chunked(update, message)


async def _send_chunked(update: Update, text: str, limit: int = 3800) -> None:
    """Send a message in chunks to stay under Telegram's 4096-char limit."""
    if len(text) <= limit:
        await update.message.reply_text(text)
        return
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    for chunk in chunks:
        await update.message.reply_text(chunk)


def _list_outbox_letters(slug: str) -> list:
    outbox = paths.agent_dir(slug) / "outbox"
    if not outbox.exists():
        return []
    files = [p for p in outbox.iterdir() if p.is_file() and p.suffix == ".md"]
    files.sort(key=lambda p: p.name, reverse=True)
    return files


async def _on_letters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    incoming_chat_id = update.message.chat_id
    expected = context.bot_data.get("expected_chat_id")
    if expected is not None and incoming_chat_id != expected:
        return
    slug = context.bot_data["slug"]
    letters = _list_outbox_letters(slug)
    if not letters:
        await update.message.reply_text("(no letters yet)")
        return
    lines = ["Letters from the agent (most recent first):"]
    for i, path in enumerate(letters[:30], start=1):
        title_line = ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                first = f.readline().strip()
            title_line = first.lstrip("# ").strip() or path.stem
        except Exception:
            title_line = path.stem
        lines.append(f"{i}. {title_line}  [{path.name}]")
    lines.append("")
    lines.append("Read one with `/letter <N>`.")
    await update.message.reply_text("\n".join(lines))


async def _on_letter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    incoming_chat_id = update.message.chat_id
    expected = context.bot_data.get("expected_chat_id")
    if expected is not None and incoming_chat_id != expected:
        return
    slug = context.bot_data["slug"]
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await update.message.reply_text("Usage: /letter <N>  (number from /letters)")
        return
    n = int(parts[1].strip())
    letters = _list_outbox_letters(slug)
    if n < 1 or n > len(letters):
        await update.message.reply_text(f"No letter #{n}. There are {len(letters)} letters.")
        return
    path = letters[n - 1]
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as e:
        await update.message.reply_text(f"Error reading {path.name}: {e}")
        return
    # Reuse the chunking helper for long letters.
    await _send_chunked(update, body)


def _on_items_command(kind: str):
    """Build a CommandHandler callback that lists items of one kind."""
    async def _handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        incoming_chat_id = update.message.chat_id
        expected = context.bot_data.get("expected_chat_id")
        if expected is not None and incoming_chat_id != expected:
            return
        slug = context.bot_data["slug"]
        items_list = ra_items.list_items(slug, kind=kind, status="open", limit=50)
        if not items_list:
            label = {"note": "notes", "important": "important", "question": "questions", "commitment": "commitments"}[kind]
            await update.message.reply_text(f"(no open {label})")
            return
        lines = [f"Open {kind}s:"] if kind != "important" else ["Open important:"]
        for i, item in enumerate(items_list, start=1):
            date = item.created_at[:10] if item.created_at else "?"
            who = "you" if item.source == "human" else "agent"
            preview = item.body if len(item.body) <= 200 else item.body[:200] + "..."
            lines.append(f"{i}. [{date} · {who}] {preview}")
        await update.message.reply_text("\n".join(lines))
    return _handler


async def fetch_chat_ids(slug: str) -> list[tuple[int, str]]:
    """Return [(chat_id, display_name), ...] from recent bot updates."""
    import httpx

    token, _ = get_telegram_creds(slug)
    if not token:
        raise ValueError("No bot token configured.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"https://api.telegram.org/bot{token}/getUpdates")
        data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data.get('description') or data}")

    seen: dict[int, str] = {}
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        name = chat.get("username") or chat.get("first_name") or "?"
        seen[cid] = name
    return list(seen.items())


async def test_bot_token(slug: str) -> str:
    """Verify the configured bot token with getMe; return the bot username."""
    import httpx

    token, _ = get_telegram_creds(slug)
    if not token:
        raise ValueError("No bot token configured.")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data.get('description') or data}")
    return data.get("result", {}).get("username", "?")


async def send_test_message(slug: str, text: str = "✓ Test message from Return Architecture.") -> None:
    """Send a one-line test message to the configured chat_id."""
    import httpx

    token, chat_id = get_telegram_creds(slug)
    if not token or chat_id is None:
        raise ValueError("Bot token and chat_id must both be set.")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data.get('description') or data}")


async def discover_chat_id(slug: str) -> None:
    """Read getUpdates once and print any chat_ids found."""
    import httpx

    token, _ = get_telegram_creds(slug)
    if not token:
        raise ValueError(
            f"No telegram bot_token in {paths.agent_secrets_path(slug)}."
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"https://api.telegram.org/bot{token}/getUpdates")
        data = resp.json()

    if not data.get("ok"):
        print(f"Error from Telegram: {data}")
        return

    updates = data.get("result", [])
    if not updates:
        print(
            "No updates yet. Open Telegram, send any message to your bot, "
            "then run this command again."
        )
        return

    seen: dict[int, str] = {}
    for upd in updates:
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        name = chat.get("username") or chat.get("first_name") or "?"
        seen[cid] = name

    if not seen:
        print("Updates exist but no chat_id found in them.")
        return

    print("Found these chat IDs:")
    for cid, name in seen.items():
        print(f"  chat_id = {cid}   ({name})")
    print()
    print(f"Add the right one to: {paths.agent_secrets_path(slug)}")
    print("Under [telegram], set:")
    print(f'  chat_id = "{next(iter(seen))}"')
