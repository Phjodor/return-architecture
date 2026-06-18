"""Combined daemon: Telegram worker + scheduler in one event loop.

This is the "run the agent for real" entry point. Both the Telegram bot
and the scheduled-ping scheduler share one agent session, one event loop,
and one asyncio.Lock that serialises calls into runtime.turn()/ping().
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from telegram import Update

from return_architecture import (
    presence_server,
    runtime,
    scheduling,
    telegram_worker,
)


def run_daemon(slug: str) -> None:
    """Blocking. Runs telegram worker and scheduler together until Ctrl-C."""
    session = runtime.build_session(slug)
    turn_lock = asyncio.Lock()
    presence_cfg = session.config.presence
    presence_runner = None

    app = telegram_worker.build_application(slug, session, turn_lock)
    sched = scheduling.AgentScheduler(session, turn_lock)
    session.scheduler = sched
    handles = sched.register_from_config()
    self_registered = sched.register_self_schedules()

    enabled = [h for h in handles if h.enabled]
    disabled = [h for h in handles if not h.enabled]

    print(f"[daemon] agent '{slug}' starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (local time).")
    print(f"[daemon] telegram: bound to chat_id {app.bot_data['expected_chat_id']}")
    if enabled:
        print(f"[daemon] schedules enabled: {', '.join(h.name + ' (' + h.cron + ')' for h in enabled)}")
    else:
        print(f"[daemon] schedules enabled: (none)")
    if disabled:
        print(f"[daemon] schedules defined but disabled: {', '.join(h.name for h in disabled)}")
    if self_registered:
        print(f"[daemon] self-schedules registered: {self_registered}")
    if presence_cfg.enabled:
        print(f"[daemon] presence app: http://{presence_cfg.address}:{presence_cfg.port}")
    print(f"[daemon] Ctrl-C to stop.")

    async def _post_init(_app):
        nonlocal presence_runner
        await telegram_worker.set_bot_commands(_app)
        sched.start()
        if presence_cfg.enabled:
            try:
                # static_dir unset → presence_server falls back to the frontend
                # bundled in the package, so any agent can enable Presence
                # without keeping its own copy.
                presence_runner = await presence_server.start(
                    slug, session, turn_lock,
                    presence_cfg.address, presence_cfg.port,
                    presence_cfg.static_dir,
                )
            except Exception as e:
                # A broken presence config must not take down Telegram.
                print(f"[daemon] presence: failed to start ({e}).")

    async def _post_shutdown(_app):
        sched.shutdown()
        if presence_runner is not None:
            await presence_runner.cleanup()
        session.close()

    app.post_init = _post_init
    app.post_shutdown = _post_shutdown
    app.run_polling(allowed_updates=Update.ALL_TYPES)
