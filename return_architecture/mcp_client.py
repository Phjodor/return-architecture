"""Synchronous MCP client over stdio.

Launches an MCP server as a subprocess and speaks JSON-RPC 2.0 over its
stdin/stdout. Synchronous by design — the runtime's tool interface is
sync, and avoiding asyncio here keeps the codepath simple.

Supports the minimal MCP surface the runtime needs: initialize handshake,
tools/list, tools/call. Resources and prompts are ignored.

Subprocesses persist for the life of the AgentSession that owns them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "return-architecture", "version": "0.1.0"}


class MCPError(Exception):
    pass


@dataclass
class MCPToolDef:
    name: str
    description: str
    input_schema: dict


def _resolve_command(command: str) -> str:
    """Convenience: 'python' → the running interpreter's path."""
    if command == "python":
        return sys.executable
    return command


class MCPServer:
    """A long-lived MCP server subprocess speaking JSON-RPC over stdio."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._command = _resolve_command(command)
        self._args = list(args or [])
        self._env_overrides = dict(env or {})
        self._proc: subprocess.Popen | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._closed = False

    def _ensure_started(self) -> None:
        if self._proc is not None:
            return
        if self._closed:
            raise MCPError(f"MCP server '{self.name}' is closed.")
        full_env = os.environ.copy()
        full_env.update(self._env_overrides)
        try:
            self._proc = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=full_env,
                bufsize=1,
            )
        except FileNotFoundError as e:
            raise MCPError(
                f"MCP server '{self.name}': command '{self._command}' not found. {e}"
            )
        self._initialize()

    def _initialize(self) -> None:
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self._notify("notifications/initialized", {})

    def _send(self, message: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        line = json.dumps(message) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as e:
            raise MCPError(f"MCP server '{self.name}' pipe broken: {e}")

    def _read_until_id(self, target_id: int) -> dict:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = self._proc.stdout.readline()
            if not line:
                # Capture stderr for context.
                stderr = ""
                if self._proc.stderr is not None:
                    try:
                        stderr = self._proc.stderr.read() or ""
                    except Exception:
                        stderr = ""
                raise MCPError(
                    f"MCP server '{self.name}' closed stdout unexpectedly. "
                    f"stderr: {stderr[:500]}"
                )
            try:
                message = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == target_id:
                return message
            # Otherwise it's a server-side notification — ignore.

    def _request(self, method: str, params: dict) -> Any:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            self._send({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            })
            response = self._read_until_id(req_id)
        if "error" in response:
            err = response["error"]
            raise MCPError(f"MCP server '{self.name}' returned error: {err}")
        return response.get("result")

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def list_tools(self) -> list[MCPToolDef]:
        self._ensure_started()
        result = self._request("tools/list", {}) or {}
        out: list[MCPToolDef] = []
        for t in result.get("tools", []):
            out.append(MCPToolDef(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
            ))
        return out

    def call_tool(self, name: str, arguments: dict) -> str:
        self._ensure_started()
        result = self._request("tools/call", {"name": name, "arguments": arguments}) or {}
        blocks = result.get("content", []) or []
        text_parts: list[str] = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "text":
                text_parts.append(b.get("text", ""))
        if text_parts:
            return "\n".join(text_parts)
        # Fallback: serialise non-text content.
        return json.dumps(result)[:5000]

    def close(self) -> None:
        self._closed = True
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
