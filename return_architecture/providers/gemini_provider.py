"""Gemini provider — Google's native generative API.

Uses the unified google-genai SDK. Supports text, vision input (images as
inline data), and tool use. Gemini 2.5 models use dynamic thinking by default,
so reasoning is enabled without additional configuration.
"""

from __future__ import annotations

import base64
from typing import Any

from google import genai
from google.genai import types

from return_architecture.providers.base import (
    Message,
    Provider,
    ProviderResponse,
    ToolCall,
)


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

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
    ) -> ProviderResponse:
        contents = [_to_gemini_content(m) for m in messages]

        config_kwargs: dict[str, Any] = {
            "max_output_tokens": max_tokens,
        }
        if system:
            config_kwargs["system_instruction"] = system
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if top_p is not None:
            config_kwargs["top_p"] = top_p
        if top_k is not None:
            config_kwargs["top_k"] = top_k
        if thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget,
            )
        if tools:
            config_kwargs["tools"] = [_to_gemini_tool(tools)]

        config = types.GenerateContentConfig(**config_kwargs)

        resp = self._client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        candidate = resp.candidates[0] if resp.candidates else None
        if candidate and candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                # Skip thinking-text parts — those are the model's private
                # reasoning, not the visible reply.
                is_thought = bool(getattr(part, "thought", False))
                if getattr(part, "text", None) and not is_thought:
                    text_parts.append(part.text)
                fc = getattr(part, "function_call", None)
                if fc:
                    args = dict(fc.args) if fc.args else {}
                    sig = getattr(part, "thought_signature", None)
                    tool_calls.append(
                        ToolCall(
                            id=fc.name,
                            name=fc.name,
                            arguments=args,
                            thought_signature=sig,
                        )
                    )

        return ProviderResponse(
            text="".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=str(getattr(candidate, "finish_reason", "") or ""),
            raw=resp,
        )


def _to_gemini_tool(tools: list[dict[str, Any]]) -> types.Tool:
    declarations: list[types.FunctionDeclaration] = []
    for t in tools:
        declarations.append(types.FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters=t.get("input_schema"),
        ))
    return types.Tool(function_declarations=declarations)


def _to_gemini_content(m: Message) -> types.Content:
    if m.role == "user":
        parts: list[types.Part] = []
        for img in m.images:
            parts.append(types.Part(
                inline_data=types.Blob(
                    mime_type=img.mime_type,
                    data=base64.b64decode(img.base64_data),
                )
            ))
        if m.content:
            parts.append(types.Part(text=m.content))
        if not parts:
            parts.append(types.Part(text=""))
        return types.Content(role="user", parts=parts)
    if m.role == "assistant":
        parts: list[types.Part] = []
        if m.content:
            parts.append(types.Part(text=m.content))
        for tc in m.tool_calls:
            part_kwargs: dict[str, Any] = {
                "function_call": types.FunctionCall(
                    name=tc.name, args=tc.arguments
                ),
            }
            # Echo the signature back so Gemini can preserve thinking context
            # across the tool loop. Required by 2.5/3.x when thinking is on.
            if tc.thought_signature is not None:
                part_kwargs["thought_signature"] = tc.thought_signature
            parts.append(types.Part(**part_kwargs))
        return types.Content(role="model", parts=parts)
    if m.role == "tool":
        return types.Content(role="user", parts=[
            types.Part(function_response=types.FunctionResponse(
                name=m.tool_call_id or "",
                response={"result": m.content or ""},
            ))
        ])
    raise ValueError(f"Unknown role: {m.role}")
