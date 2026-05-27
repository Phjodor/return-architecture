"""Path resolution for the install root and per-agent folders.

The install root defaults to ~/return-architecture but can be overridden via
the RA_INSTALL_ROOT environment variable — useful for development against
a separate data directory.
"""

from __future__ import annotations

import os
from pathlib import Path


def install_root() -> Path:
    override = os.environ.get("RA_INSTALL_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "return-architecture"


def install_config_path() -> Path:
    return install_root() / "config.toml"


def install_secrets_path() -> Path:
    return install_root() / "secrets.toml"


def agents_dir() -> Path:
    return install_root() / "agents"


def agent_dir(slug: str) -> Path:
    return agents_dir() / slug


def agent_config_path(slug: str) -> Path:
    return agent_dir(slug) / "config.toml"


def agent_secrets_path(slug: str) -> Path:
    return agent_dir(slug) / "secrets.toml"


def agent_system_prompt_path(slug: str) -> Path:
    return agent_dir(slug) / "system_prompt.md"


def agent_logs_dir(slug: str) -> Path:
    return agent_dir(slug) / "logs"


def conversation_log_path(slug: str, date_str: str) -> Path:
    return agent_logs_dir(slug) / f"conversations-{date_str}.ndjson"


# Per-agent subdirectories created at init time.
AGENT_SUBDIRS = [
    "memory",
    "private",
    "outbox",
    "inbox",
    "artifacts",
    "artifacts/incoming",
    "artifacts/notes",
    "artifacts/shared",
    "sessions",
    "reflections",
    "logs",
]
