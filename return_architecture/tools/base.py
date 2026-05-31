"""Interface for built-in tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolContext:
    """Minimal context passed to every tool execution.

    Tools should read whatever else they need (secrets, paths) from disk
    based on the slug, rather than receiving more state directly.

    `scheduler` is populated when the session is running under the daemon
    (so tools like schedule_self can mutate the live job store). It is
    None for one-shot CLI chat sessions; tools that need it should refuse
    politely.
    """
    slug: str
    session_id: str
    scheduler: Any = None


@dataclass
class ToolResult:
    content: str
    is_silence: bool = False


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]

    @abstractmethod
    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult: ...

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
