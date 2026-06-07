"""Gemini provider — Google's native generative API.

Uses the unified google-genai SDK. Supports text, vision input (images as
inline data), and tool use. Gemini 2.5 models use dynamic thinking by default,
so reasoning is enabled without additional configuration.

Gemini 3 adds support for combining built-in tools (google_search, url_context,
code_execution, google_maps, file_search, computer_use) with function calling
in the same request. The runtime opts in per agent via model.native_tools in
config.toml.
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
        native_tools: list[str] | None = None,
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
        tool_objects: list[types.Tool] = []
        if tools:
            tool_objects.append(_to_gemini_tool(tools))
        native_added = False
        for name in native_tools or []:
            native = _build_native_tool(name)
            if native is not None:
                tool_objects.append(native)
                native_added = True
        if tool_objects:
            config_kwargs["tools"] = tool_objects
        # Gemini 3 requires this flag whenever built-in tools coexist with
        # function-calling tools in the same request. Without it the API
        # returns 400 INVALID_ARGUMENT. Setting it whenever any native tool
        # is enabled is the safe superset.
        if native_added and tools:
            try:
                config_kwargs["tool_config"] = types.ToolConfig(
                    include_server_side_tool_invocations=True,
                )
            except (AttributeError, TypeError):
                pass

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


_NATIVE_TOOL_BUILDERS: dict[str, Any] = {
    "google_search": lambda: types.Tool(google_search=types.GoogleSearch()),
    "url_context": lambda: types.Tool(url_context=types.UrlContext()),
    "code_execution": lambda: types.Tool(code_execution=types.ToolCodeExecution()),
    "google_maps": lambda: types.Tool(google_maps=types.GoogleMaps()),
    "file_search": lambda: types.Tool(file_search=types.FileSearch()),
    "computer_use": lambda: types.Tool(computer_use=types.ComputerUse()),
}


def _build_native_tool(name: str) -> types.Tool | None:
    builder = _NATIVE_TOOL_BUILDERS.get(name)
    if builder is None:
        return None
    try:
        return builder()
    except (AttributeError, TypeError):
        # SDK version may not expose this tool yet — skip silently rather
        # than crashing the whole turn.
        return None


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
