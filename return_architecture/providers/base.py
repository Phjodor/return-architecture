"""Provider-neutral interface for LLM calls.

A Provider takes a normalised list of messages and tool schemas, returns
either text content, tool calls, or both. The runtime is responsible for
executing tool calls and continuing the loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Provider-specific opaque blob. Gemini 2.5/3.x returns a thought_signature
    # on the function_call part during thinking mode, and requires it echoed
    # back verbatim on the next request. Other providers leave this None.
    thought_signature: bytes | None = None


@dataclass
class ImageContent:
    """An inline image attached to a message.

    base64_data is the raw bytes already base64-encoded as a string.
    mime_type is e.g. 'image/jpeg', 'image/png'.
    """
    base64_data: str
    mime_type: str


@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # For role=="tool":
    tool_call_id: str | None = None
    # For role=="user" — inline images delivered alongside the text.
    images: list[ImageContent] = field(default_factory=list)


@dataclass
class ProviderResponse:
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str
    raw: Any = None


class Provider(ABC):
    name: str

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float | None,
        top_p: float | None = None,
        top_k: int | None = None,
        thinking_budget: int | None = None,
        native_tools: list[str] | None = None,
    ) -> ProviderResponse: ...
