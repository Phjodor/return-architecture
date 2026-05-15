"""Agent loop.

Reads agent config, builds the provider, tools, and memory, runs a
turn-by-turn conversation. Each turn:
  1. recall relevant memories based on the user's message
  2. send messages + augmented system + tools to the provider
  3. if the response has tool calls, execute them, append results, loop
  4. otherwise return the text content (which may be empty if the agent
     chose silence via no_response)
  5. store the user message and assistant text in memory
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from return_architecture import config as cfg
from return_architecture import logging as ralog
from return_architecture import memory as ramem
from return_architecture.mcp_client import MCPError, MCPServer
from return_architecture.providers import ImageContent, Message, Provider, ToolCall
from return_architecture.providers.anthropic_provider import AnthropicProvider
from return_architecture.providers.openai_provider import OpenAIProvider
from return_architecture.tools import BUILTIN_TOOLS, Tool
from return_architecture.tools.base import ToolContext, ToolResult
from return_architecture.tools.mcp_proxy import MCPProxyTool


MAX_TOOL_LOOPS = 8
MEMORY_RECALL_TOP_K = 5


@dataclass
class AgentSession:
    slug: str
    session_id: str
    config: cfg.AgentConfig
    base_system_prompt: str
    provider: Provider
    tools: dict[str, Tool]
    memory: ramem.MemoryStore
    messages: list[Message]
    mcp_servers: dict[str, MCPServer] = field(default_factory=dict)

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools.values()]

    def close(self) -> None:
        for server in self.mcp_servers.values():
            try:
                server.close()
            except Exception:
                pass
        self.mcp_servers.clear()


def build_session(slug: str) -> AgentSession:
    agent_cfg = cfg.load_agent_config(slug)
    secrets = cfg.load_install_secrets()
    system_prompt = cfg.load_system_prompt(slug)

    provider = _build_provider(agent_cfg.model.provider, secrets)
    tools = _build_tools(agent_cfg.tools.enabled)
    memory = ramem.MemoryStore(slug)

    mcp_servers, mcp_tools = _start_mcp_servers(slug, agent_cfg.mcp.servers)
    # Built-in tools take precedence over any same-named MCP tool.
    for name, tool in mcp_tools.items():
        if name in tools:
            ralog.log_event(slug, "mcp_tool_name_collision", {"tool": name})
            continue
        tools[name] = tool

    return AgentSession(
        slug=slug,
        session_id=ramem.new_session_id(),
        config=agent_cfg,
        base_system_prompt=system_prompt,
        provider=provider,
        tools=tools,
        memory=memory,
        messages=[],
        mcp_servers=mcp_servers,
    )


def _start_mcp_servers(
    slug: str,
    server_configs: dict[str, cfg.MCPServerConfig],
) -> tuple[dict[str, MCPServer], dict[str, Tool]]:
    servers: dict[str, MCPServer] = {}
    tools: dict[str, Tool] = {}
    for name, sc in server_configs.items():
        server = MCPServer(name=name, command=sc.command, args=sc.args, env=sc.env)
        try:
            tool_defs = server.list_tools()
        except MCPError as e:
            ralog.log_event(slug, "mcp_server_start_failed", {
                "server": name, "error": str(e),
            })
            try:
                server.close()
            except Exception:
                pass
            continue
        servers[name] = server
        for tdef in tool_defs:
            tools[tdef.name] = MCPProxyTool(
                server=server,
                name=tdef.name,
                description=tdef.description,
                parameters=tdef.input_schema,
            )
        ralog.log_event(slug, "mcp_server_started", {
            "server": name, "tools": [t.name for t in tool_defs],
        })
    return servers, tools


def turn(
    session: AgentSession,
    user_input: str,
    images: list[ImageContent] | None = None,
) -> str:
    """One round-trip: user says something, agent responds (possibly via tools).

    Optional inline images are sent with the user message for vision-capable
    models. Images are NOT written to the conversation log or to memory —
    only a marker noting that one was present.
    """
    images = images or []
    session.messages.append(Message(role="user", content=user_input, images=images))
    ralog.log_event(session.slug, "user_message", {
        "content": user_input,
        "images": len(images),
    })

    recalled = session.memory.recall(user_input, top_k=MEMORY_RECALL_TOP_K)
    augmented_system = _augment_system_prompt(session.base_system_prompt, recalled)
    if recalled:
        ralog.log_event(session.slug, "memory_recall", {
            "query": user_input,
            "hits": [
                {"role": m.role, "ts": m.timestamp, "distance": m.distance,
                 "excerpt": m.content[:200]}
                for m in recalled
            ],
        })

    assistant_text: str | None = None

    for _ in range(MAX_TOOL_LOOPS):
        resp = session.provider.complete(
            system=augmented_system,
            messages=session.messages,
            tools=session.tool_schemas(),
            model=session.config.model.name,
            max_tokens=session.config.model.max_tokens,
            temperature=session.config.model.temperature,
        )

        session.messages.append(Message(
            role="assistant",
            content=resp.text,
            tool_calls=resp.tool_calls,
        ))
        ralog.log_event(session.slug, "assistant_message", {
            "text": resp.text,
            "tool_calls": [{"name": tc.name, "args": tc.arguments} for tc in resp.tool_calls],
            "stop_reason": resp.stop_reason,
        })

        if resp.text:
            assistant_text = resp.text

        if not resp.tool_calls:
            _store_turn(session, user_input, assistant_text)
            return resp.text or ""

        chose_silence = False
        for tc in resp.tool_calls:
            result = _execute_tool(session, tc)
            session.messages.append(Message(
                role="tool",
                content=result.content,
                tool_call_id=tc.id,
            ))
            ralog.log_event(session.slug, "tool_result", {
                "tool": tc.name,
                "content": result.content,
                "is_silence": result.is_silence,
            })
            if result.is_silence:
                chose_silence = True

        if chose_silence:
            _store_turn(session, user_input, assistant_text)
            return ""

    _store_turn(session, user_input, assistant_text)
    return "(stopped: tool loop limit reached)"


def ping(session: AgentSession, ping_name: str, prompt: str) -> str:
    """A scheduled wake. The agent is invited to act; nothing is auto-sent.

    The agent can choose silence, write privately (later tools), or reach
    out via send_to_human_telegram. The return value is the final text the
    agent produced; the caller typically just logs it.
    """
    framed = f"[scheduled ping: {ping_name}]\n\n{prompt}"
    session.messages.append(Message(role="user", content=framed))
    ralog.log_event(session.slug, "scheduled_ping", {
        "ping_name": ping_name,
        "prompt": prompt,
    })

    recalled = session.memory.recall(prompt, top_k=MEMORY_RECALL_TOP_K)
    augmented_system = _augment_system_prompt(session.base_system_prompt, recalled)

    assistant_text: str | None = None

    for _ in range(MAX_TOOL_LOOPS):
        resp = session.provider.complete(
            system=augmented_system,
            messages=session.messages,
            tools=session.tool_schemas(),
            model=session.config.model.name,
            max_tokens=session.config.model.max_tokens,
            temperature=session.config.model.temperature,
        )

        session.messages.append(Message(
            role="assistant",
            content=resp.text,
            tool_calls=resp.tool_calls,
        ))
        ralog.log_event(session.slug, "assistant_message", {
            "text": resp.text,
            "tool_calls": [{"name": tc.name, "args": tc.arguments} for tc in resp.tool_calls],
            "stop_reason": resp.stop_reason,
            "from_ping": ping_name,
        })

        if resp.text:
            assistant_text = resp.text

        if not resp.tool_calls:
            _store_ping(session, ping_name, assistant_text)
            return resp.text or ""

        chose_silence = False
        for tc in resp.tool_calls:
            result = _execute_tool(session, tc)
            session.messages.append(Message(
                role="tool",
                content=result.content,
                tool_call_id=tc.id,
            ))
            ralog.log_event(session.slug, "tool_result", {
                "tool": tc.name,
                "content": result.content,
                "is_silence": result.is_silence,
                "from_ping": ping_name,
            })
            if result.is_silence:
                chose_silence = True

        if chose_silence:
            _store_ping(session, ping_name, assistant_text)
            return ""

    _store_ping(session, ping_name, assistant_text)
    return "(stopped: tool loop limit reached)"


def _store_ping(session: AgentSession, ping_name: str, assistant_text: str | None) -> None:
    if assistant_text:
        session.memory.remember(
            assistant_text, role="assistant", session_id=session.session_id
        )


def _store_turn(session: AgentSession, user_input: str, assistant_text: str | None) -> None:
    session.memory.remember(user_input, role="user", session_id=session.session_id)
    if assistant_text:
        session.memory.remember(assistant_text, role="assistant", session_id=session.session_id)


def _augment_system_prompt(
    base: str,
    memories: list[ramem.MemoryEntry],
) -> str:
    if not memories:
        return base
    lines: list[str] = []
    for m in memories:
        date = m.timestamp[:10] if m.timestamp else "earlier"
        speaker = "you" if m.role == "assistant" else "the human"
        lines.append(f"- [{date} · {speaker}] {m.content}")
    block = "\n".join(lines)
    return (
        f"{base}\n\n"
        f"---\n\n"
        f"What you remember from past sessions, most relevant to this moment "
        f"(not necessarily recent):\n\n{block}\n\n"
        f"Treat these as recollection — context you carry, not a transcript "
        f"you must respond to. If they don't fit, let them pass."
    )


def _execute_tool(session: AgentSession, tc: ToolCall) -> ToolResult:
    tool = session.tools.get(tc.name)
    if tool is None:
        return ToolResult(content=f"Error: unknown tool '{tc.name}'")
    ctx = ToolContext(slug=session.slug, session_id=session.session_id)
    return tool.execute(tc.arguments, ctx)


def _build_provider(name: str, secrets: cfg.InstallSecrets) -> Provider:
    if name == "anthropic":
        key = secrets.providers.anthropic
        if not key:
            raise ValueError("Anthropic API key missing from install secrets.toml")
        return AnthropicProvider(api_key=key)
    if name == "openai":
        key = secrets.providers.openai
        if not key:
            raise ValueError("OpenAI API key missing from install secrets.toml")
        return OpenAIProvider(api_key=key)
    raise ValueError(f"Unsupported provider: {name}")


def _build_tools(enabled: list[str]) -> dict[str, Tool]:
    tools: dict[str, Tool] = {}
    if "no_response" not in enabled:
        enabled = ["no_response", *enabled]
    for name in enabled:
        tool = BUILTIN_TOOLS.get(name)
        if tool is None:
            continue
        tools[name] = tool
    return tools
