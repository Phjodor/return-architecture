"""OpenAI provider."""

from __future__ import annotations

import json
from typing import Any

import openai

from return_architecture.providers.base import (
    Message,
    Provider,
    ProviderResponse,
    ToolCall,
)


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str) -> None:
        self._client = openai.OpenAI(api_key=api_key)

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float | None,
    ) -> ProviderResponse:
        oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            oai_messages.extend(_to_openai_messages(m))

        oai_tools = [_to_openai_tool(t) for t in tools] if tools else None

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "tools": oai_tools,
            "max_completion_tokens": max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = self._client.chat.completions.create(**kwargs)

        choice = resp.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            arguments: dict[str, Any]
            try:
                arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))

        return ProviderResponse(
            text=msg.content,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "",
            raw=resp,
        )


def _to_openai_tool(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }


def _to_openai_messages(m: Message) -> list[dict[str, Any]]:
    if m.role == "user":
        if m.images:
            content: list[dict[str, Any]] = []
            for img in m.images:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img.mime_type};base64,{img.base64_data}",
                    },
                })
            content.append({"type": "text", "text": m.content or ""})
            return [{"role": "user", "content": content}]
        return [{"role": "user", "content": m.content or ""}]
    if m.role == "assistant":
        out: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
        if m.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in m.tool_calls
            ]
        return [out]
    if m.role == "tool":
        return [{
            "role": "tool",
            "tool_call_id": m.tool_call_id,
            "content": m.content or "",
        }]
    raise ValueError(f"Unknown role: {m.role}")
