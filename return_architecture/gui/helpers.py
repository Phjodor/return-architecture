"""Shared helpers for GUI pages.

Thin wrappers over the on-disk config files. The GUI is a renderer; these
helpers do the reading and writing.
"""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from return_architecture import paths


def list_agents() -> list[str]:
    """Return the slugs of all agents on disk, alphabetical."""
    agents_dir = paths.agents_dir()
    if not agents_dir.exists():
        return []
    return sorted(d.name for d in agents_dir.iterdir() if d.is_dir())


def install_root_exists() -> bool:
    return paths.install_root().exists()


# ── Install-wide config ───────────────────────────────────────────────────

def load_install_config_raw() -> dict[str, Any]:
    path = paths.install_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def write_install_config(data: dict[str, Any]) -> None:
    path = paths.install_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


# ── Install-wide secrets ──────────────────────────────────────────────────

def load_install_secrets_raw() -> dict[str, Any]:
    path = paths.install_secrets_path()
    if not path.exists():
        return {"providers": {"anthropic": "", "openai": "", "gemini": ""}}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {"providers": {"anthropic": "", "openai": "", "gemini": ""}}


def write_install_secrets(data: dict[str, Any]) -> None:
    path = paths.install_secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def provider_key_set(secrets: dict[str, Any], provider: str) -> bool:
    val = (secrets.get("providers") or {}).get(provider, "") or ""
    return bool(val.strip())


def set_provider_key(secrets: dict[str, Any], provider: str, key: str) -> None:
    providers = secrets.setdefault("providers", {})
    providers[provider] = key.strip()


# ── Per-agent ─────────────────────────────────────────────────────────────

def load_agent_config_raw(slug: str) -> dict[str, Any]:
    path = paths.agent_config_path(slug)
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def write_agent_config(slug: str, data: dict[str, Any]) -> None:
    path = paths.agent_config_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def load_system_prompt(slug: str) -> str:
    path = paths.agent_system_prompt_path(slug)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_system_prompt(slug: str, content: str) -> None:
    path = paths.agent_system_prompt_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def last_log_timestamp(slug: str) -> str | None:
    """ISO timestamp of the latest event in the agent's conversation logs."""
    import json
    logs_dir = paths.agent_logs_dir(slug)
    if not logs_dir.exists():
        return None
    files = sorted(logs_dir.glob("conversations-*.ndjson"))
    if not files:
        return None
    latest = files[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            last_line = ""
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return None
        return json.loads(last_line).get("ts")
    except (OSError, json.JSONDecodeError):
        return None


def list_outbox(slug: str) -> list[Path]:
    outbox = paths.agent_dir(slug) / "outbox"
    if not outbox.exists():
        return []
    return [p for p in outbox.iterdir() if p.is_file() and p.suffix == ".md"]


def list_inbox(slug: str) -> list[Path]:
    inbox = paths.agent_dir(slug) / "inbox"
    if not inbox.exists():
        return []
    return [p for p in inbox.iterdir() if p.is_file()]


def load_agent_secrets_raw(slug: str) -> dict[str, Any]:
    path = paths.agent_secrets_path(slug)
    if not path.exists():
        return {"telegram": {"bot_token": "", "chat_id": ""}}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {"telegram": {"bot_token": "", "chat_id": ""}}


def write_agent_secrets(slug: str, data: dict[str, Any]) -> None:
    path = paths.agent_secrets_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
