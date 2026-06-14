"""HTTP front end for an agent: the Presence chat app.

Serves the static frontend and a `POST /api/chat` SSE endpoint that runs the
agent's *real* turn (its model, identity, memory recall/store) and peels the
`<presence>` state side-channel out of the reply before streaming the prose.

This runs inside the daemon's event loop, sharing the one `AgentSession` and
`turn_lock` with the Telegram worker and the scheduler — so the web app and
Telegram are the same agent, one continuous context and memory, and turns from
all channels serialise through the same lock.

The backend contract this satisfies lives with the frontend, in the agent's
`code/RA-chat-app/CONTRACT.md`. The original `server.js` was a dev shim that
spoke to Claude directly; this replaces it for real use.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from aiohttp import web

from return_architecture import logging as ralog
from return_architecture import runtime


# What gets appended to the agent's assembled system prompt for Presence turns
# only. The agent's identity/persona comes from its own system prompt; this adds
# just the visual-body mechanics, per CONTRACT.md.
PRESENCE_INSTRUCTION = (
    "You have a visual body in this space — a living form whose colour, warmth, "
    "energy, and shape the person can see. You control it by setting parameters, "
    "never by describing it in words (never write things like \"I glow\" or \"my "
    "colours shift\" — that would be roleplay). Begin every response with exactly "
    "one presence block and nothing before it:\n\n"
    "<presence>{\"valence\": <number -1..1>, \"energy\": <number 0..1>, "
    "\"focus\": <number 0..1>, \"note\": \"<one or two words>\"}</presence>\n\n"
    "valence = heavy/cool (-1) to warm/light (1); energy = still (0) to vivid "
    "(1); focus = diffuse/open (0) to gathered (1); note = one or two plain "
    "words for the state (e.g. \"settling\", \"curious\", \"tender\"). Let the "
    "state be honest and shift it gradually across the conversation rather than "
    "resetting each turn — small movements are good. After the presence block, "
    "write your reply as ordinary text, without meta-commentary about your process."
)

_PRESENCE_RE = re.compile(r"<presence>\s*(\{.*?\})\s*</presence>", re.DOTALL)


def _split_presence(text: str) -> tuple[dict[str, Any] | None, str]:
    """Pull the first <presence>{…}</presence> block out of a reply.

    Returns (presence_dict_or_None, prose_without_the_block). Malformed JSON or
    a missing block leaves the prose untouched and returns None — the frontend
    just holds the visual's current state, exactly as the contract specifies.
    """
    m = _PRESENCE_RE.search(text)
    if not m:
        return None, text
    presence: dict[str, Any] | None
    try:
        presence = json.loads(m.group(1))
    except json.JSONDecodeError:
        presence = None
    prose = (text[: m.start()] + text[m.end():]).lstrip("\n")
    return presence, prose


def _last_user_message(messages: list[dict[str, Any]]) -> str | None:
    for m in reversed(messages):
        if m.get("role") == "user" and m.get("content"):
            return str(m["content"])
    return None


async def _handle_chat(request: web.Request) -> web.StreamResponse:
    slug = request.app["slug"]
    session = request.app["session"]
    turn_lock: asyncio.Lock = request.app["turn_lock"]

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return web.json_response({"error": "messages array required"}, status=400)
    user_input = _last_user_message(messages)
    if not user_input:
        return web.json_response({"error": "no user message found"}, status=400)

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)

    async def send(event: str, data: Any) -> None:
        await resp.write(
            f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
        )

    ralog.log_event(slug, "presence_message_in", {"content": user_input})
    try:
        # Serialise with Telegram + scheduler; run the blocking turn off-loop.
        async with turn_lock:
            reply = await asyncio.to_thread(
                runtime.turn, session, user_input, extra_system=PRESENCE_INSTRUCTION
            )
        presence, prose = _split_presence(reply or "")
        if presence is not None:
            await send("presence", presence)
        if prose:
            await send("token", {"delta": prose})
        await send("done", {"stop_reason": "end_turn"})
        ralog.log_event(slug, "presence_message_out", {
            "text": prose, "had_presence": presence is not None,
        })
    except Exception as e:  # noqa: BLE001 — surface any failure to the client
        ralog.log_event(slug, "presence_chat_error", {"error": repr(e)})
        try:
            await send("error", {"message": str(e)})
        except Exception:
            pass
    finally:
        with contextlib.suppress(Exception):
            await resp.write_eof()
    return resp


async def _handle_third_thing(request: web.Request) -> web.Response:
    """Surface one real, random memory from the agent's database.

    The "quiet memory" button. Unlike the dev shim (which synthesized a memory
    from the chat history), this pulls an actual past turn from the agent's
    ChromaDB store and frames it lightly. Returns plain JSON {"memory": str}.
    """
    slug = request.app["slug"]
    session = request.app["session"]
    turn_lock: asyncio.Lock = request.app["turn_lock"]

    try:
        # Share the lock with turns so the Chroma collection isn't read and
        # written from two threads at once. The read itself is quick.
        async with turn_lock:
            entry = await asyncio.to_thread(session.memory.random_entry)
    except Exception as e:  # noqa: BLE001
        ralog.log_event(slug, "presence_third_thing_error", {"error": repr(e)})
        return web.json_response({"error": str(e)}, status=500)

    if entry is None:
        return web.json_response(
            {"memory": "A quiet memory surfaces — but the well is still empty. "
                       "We haven't made enough here yet."}
        )

    speaker = "Arden" if entry.role == "assistant" else "you"
    when = entry.timestamp[:10] if entry.timestamp else "some time ago"
    text = entry.content.strip()
    if len(text) > 320:
        text = text[:320].rstrip() + "…"
    memory = f"A quiet memory surfaces — {speaker}, {when}:\n\n“{text}”"
    ralog.log_event(slug, "presence_third_thing", {
        "role": entry.role, "timestamp": entry.timestamp,
    })
    return web.json_response({"memory": memory})


def build_app(
    slug: str,
    session: Any,
    turn_lock: asyncio.Lock,
    static_dir: Path,
) -> web.Application:
    app = web.Application()
    app["slug"] = slug
    app["session"] = session
    app["turn_lock"] = turn_lock

    index_file = static_dir / "index.html"

    async def _index(_request: web.Request) -> web.StreamResponse:
        return web.FileResponse(index_file)

    # Explicit routes win over the static catch-all (registered last).
    app.router.add_post("/api/chat", _handle_chat)
    app.router.add_post("/api/third-thing", _handle_third_thing)
    app.router.add_get("/", _index)
    app.router.add_static("/", path=str(static_dir), show_index=False)
    return app


async def start(
    slug: str,
    session: Any,
    turn_lock: asyncio.Lock,
    address: str,
    port: int,
    static_dir: str,
) -> web.AppRunner:
    """Start the Presence server on the current event loop. Returns the runner
    so the caller can clean it up on shutdown."""
    root = Path(static_dir).expanduser().resolve()
    if not (root / "index.html").is_file():
        raise FileNotFoundError(
            f"Presence static_dir has no index.html: {root}"
        )
    app = build_app(slug, session, turn_lock, root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, address, port)
    await site.start()
    ralog.log_event(slug, "presence_server_started", {
        "address": address, "port": port, "static_dir": str(root),
    })
    return runner
