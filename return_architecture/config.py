"""Config loading and validation for install and per-agent settings."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from return_architecture import paths


# ── Install-wide ────────────────────────────────────────────────────────────

class InstallSection(BaseModel):
    created_at: str
    default_agent: str | None = None


class GuiSection(BaseModel):
    port: int = 7878
    open_browser_on_start: bool = True


class UiSection(BaseModel):
    show_cost_estimates: bool = True


class LogsSection(BaseModel):
    retention_days: int = 90


class InstallConfig(BaseModel):
    install: InstallSection
    gui: GuiSection = Field(default_factory=GuiSection)
    ui: UiSection = Field(default_factory=UiSection)
    logs: LogsSection = Field(default_factory=LogsSection)


class ProviderSecrets(BaseModel):
    anthropic: str | None = None
    openai: str | None = None


class InstallSecrets(BaseModel):
    providers: ProviderSecrets


# ── Per-agent ───────────────────────────────────────────────────────────────

Provider = Literal["anthropic", "openai"]


class AgentSection(BaseModel):
    name: str
    slug: str


class ModelSection(BaseModel):
    provider: Provider
    name: str
    max_tokens: int = 4096
    # None means "let the provider use its default" — useful for models
    # (e.g. some OpenAI reasoning models) that only accept the default.
    temperature: float | None = None


class BehaviorSection(BaseModel):
    silence_allowed: bool = True
    max_self_scheduled_jobs_per_day: int = 5


class ArtifactExchangeSection(BaseModel):
    enabled: bool = True
    mediator_provider: Provider = "anthropic"
    mediator_model: str = "claude-sonnet-4-6"
    agent_max_tokens: int = 600
    mediator_max_tokens: int = 600


class ToolsSection(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: [
        "no_response", "send_to_human_telegram",
        "artifact_delete_reaction", "artifact_share_more",
        "tag_item", "write_letter",
    ])
    artifact_exchange: ArtifactExchangeSection = Field(default_factory=ArtifactExchangeSection)


class ScheduleEntry(BaseModel):
    enabled: bool = False
    cron: str
    prompt: str
    kind: str = "regular"


class MCPServerConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class MCPSection(BaseModel):
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    agent: AgentSection
    model: ModelSection
    behavior: BehaviorSection = Field(default_factory=BehaviorSection)
    tools: ToolsSection = Field(default_factory=ToolsSection)
    schedules: dict[str, ScheduleEntry] = Field(default_factory=dict)
    mcp: MCPSection = Field(default_factory=MCPSection)


# ── Loaders ──────────────────────────────────────────────────────────────────

def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Expected config at {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_install_config() -> InstallConfig:
    return InstallConfig.model_validate(_read_toml(paths.install_config_path()))


def load_install_secrets() -> InstallSecrets:
    return InstallSecrets.model_validate(_read_toml(paths.install_secrets_path()))


def load_agent_config(slug: str) -> AgentConfig:
    return AgentConfig.model_validate(_read_toml(paths.agent_config_path(slug)))


def load_system_prompt(slug: str) -> str:
    path = paths.agent_system_prompt_path(slug)
    return path.read_text(encoding="utf-8").strip()
