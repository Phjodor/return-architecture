"""Anthropic provider."""

from __future__ import annotations

import json
from typing import Any

import anthropic

from return_architecture.providers.base import (
    Message,
    Provider,
    ProviderResponse,
    ToolCall,
)


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)

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
    ) -> ProviderResponse:
        anthropic_messages = [_to_anthropic_message(m) for m in messages]
        kwargs: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": anthropic_messages,
            "tools": tools or anthropic.NOT_GIVEN,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        if top_k is not None:
            kwargs["top_k"] = top_k
        resp = self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input)
                )

        return ProviderResponse(
            text="".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason or "",
            raw=resp,
        )


def _to_anthropic_message(m: Message) -> dict[str, Any]:
    if m.role == "user":
        if m.images:
            blocks: list[dict[str, Any]] = []
            for img in m.images:
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.mime_type,
                        "data": img.base64_data,
                    },
                })
            blocks.append({"type": "text", "text": m.content or ""})
            return {"role": "user", "content": blocks}
        return {"role": "user", "content": m.content or ""}
    if m.role == "assistant":
        blocks: list[dict[str, Any]] = []
        if m.content:
            blocks.append({"type": "text", "text": m.content})
        for tc in m.tool_calls:
            blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.arguments,
            })
        return {"role": "assistant", "content": blocks}
    if m.role == "tool":
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": m.tool_call_id,
                "content": m.content or "",
            }],
        }
    raise ValueError(f"Unknown role: {m.role}")
