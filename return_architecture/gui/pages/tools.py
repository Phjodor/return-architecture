"""Tools editor — built-in toggles and MCP server configuration."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from return_architecture import service as ra_service
from return_architecture.gui import helpers
from return_architecture.tools import BUILTIN_TOOLS


# Tools that cannot be disabled — silence as a deliberate action is core.
REQUIRED_TOOLS = {"no_response"}

# Bundled MCP servers — friendlier setup form when adding these.
BUNDLED_SERVERS: dict[str, dict[str, Any]] = {
    "url_fetch": {
        "command": "python",
        "args":    ["-m", "return_architecture.mcp_servers.url_fetch"],
        "description": (
            "Fetches a URL and returns readable text content. Use when the "
            "agent needs to read an article or page the human references."
        ),
    },
    "filesystem": {
        "command": "python",
        "args":    ["-m", "return_architecture.mcp_servers.filesystem"],
        "description": (
            "Read/write access to one folder you point it at. Path-traversal "
            "blocked. Use for a notes / writing / library folder you want "
            "the agent to be able to read or contribute to."
        ),
    },
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def render() -> None:
    slug = st.session_state.get("agent_slug")
    if not slug:
        st.warning("No agent selected. Use the sidebar.")
        return

    st.title(f"Tools — {slug}")
    st.caption(
        "Built-in tools ship with Return Architecture. MCP servers are external "
        "tools the agent can call — each one exposes one or more named tools. "
        "Changes take effect after a service restart."
    )

    config = helpers.load_agent_config_raw(slug)

    _render_builtin(slug, config)
    st.divider()
    _render_mcp(slug, config)
    st.divider()

    if st.button("Reload service to apply changes", key="_reload_for_tools"):
        try:
            with st.spinner("Restarting service…"):
                ra_service.restart(slug)
            st.success("Service restarted.")
        except (RuntimeError, FileNotFoundError) as e:
            st.error(f"Restart failed: {e}")


# ── Built-in tools ────────────────────────────────────────────────────────

def _render_builtin(slug: str, config: dict) -> None:
    st.subheader("Built-in tools")
    st.caption(
        "Each toggle controls whether the tool appears in the agent's "
        "available toolkit. Disabled tools are simply not offered to the model."
    )

    tools_section = config.get("tools") or {}
    currently_enabled = set(tools_section.get("enabled", []))

    new_enabled: set[str] = set(REQUIRED_TOOLS)

    for name, tool in BUILTIN_TOOLS.items():
        is_required = name in REQUIRED_TOOLS
        is_on = is_required or (name in currently_enabled)
        label = f"**{name}**" + (" *(always on)*" if is_required else "")
        checked = st.checkbox(
            label,
            value=is_on,
            disabled=is_required,
            help=tool.description,
            key=f"_builtin_{name}",
        )
        if checked and not is_required:
            new_enabled.add(name)

    if st.button("Save built-in tool selection", key="_save_builtin"):
        config.setdefault("tools", {})["enabled"] = sorted(new_enabled)
        helpers.write_agent_config(slug, config)
        st.success("Saved. Restart the service below to apply.")


# ── MCP servers ───────────────────────────────────────────────────────────

def _render_mcp(slug: str, config: dict) -> None:
    st.subheader("MCP servers")
    st.caption(
        "External tools. Each MCP server is a separate process — bundled "
        "ones live inside Return Architecture; custom ones can be anything "
        "you point this at."
    )

    mcp_section = config.get("mcp") or {}
    servers = mcp_section.get("servers") or {}

    if not servers:
        st.info("No MCP servers configured yet.")
    else:
        for name in list(servers.keys()):
            entry = servers[name]
            with st.expander(f"{name}", expanded=False):
                _render_server_editor(slug, config, name, entry)

    # Add buttons
    add_cols = st.columns([1, 1, 5])
    if add_cols[0].button("➕ Add bundled", key="_add_bundled_btn"):
        st.session_state["_mcp_add_mode"] = "bundled"
        st.rerun()
    if add_cols[1].button("➕ Add custom", key="_add_custom_btn"):
        st.session_state["_mcp_add_mode"] = "custom"
        st.rerun()

    mode = st.session_state.get("_mcp_add_mode")
    if mode == "bundled":
        _render_add_bundled(slug, config, servers)
    elif mode == "custom":
        _render_add_custom(slug, config, servers)


def _detect_bundled_kind(entry: dict) -> str | None:
    """Return 'url_fetch' or 'filesystem' if `entry` looks like a bundled
    server, otherwise None."""
    command = entry.get("command", "")
    args = list(entry.get("args") or [])
    if command != "python":
        return None
    # Expecting: ["-m", "return_architecture.mcp_servers.X", ...maybe more...]
    if len(args) < 2 or args[0] != "-m":
        return None
    module = args[1]
    if module == "return_architecture.mcp_servers.url_fetch":
        return "url_fetch"
    if module == "return_architecture.mcp_servers.filesystem":
        return "filesystem"
    return None


def _render_server_editor(slug: str, config: dict, name: str, entry: dict) -> None:
    bundled = _detect_bundled_kind(entry)
    if bundled == "url_fetch":
        _render_url_fetch_editor(slug, config, name, entry)
    elif bundled == "filesystem":
        _render_filesystem_editor(slug, config, name, entry)
    else:
        _render_raw_editor(slug, config, name, entry)
    _render_delete_button(slug, config, name)


def _render_url_fetch_editor(slug: str, config: dict, name: str, entry: dict) -> None:
    st.info("Bundled URL-fetch server. No configuration needed.")
    st.caption(
        "Exposes `fetch_url(url)` — returns readable text content of a web page."
    )


def _render_filesystem_editor(slug: str, config: dict, name: str, entry: dict) -> None:
    st.info("Bundled filesystem server. Scoped to one folder.")
    args = list(entry.get("args") or [])
    # args = ["-m", "return_architecture.mcp_servers.filesystem", <path>, optionally "--read-only"]
    current_path = args[2] if len(args) >= 3 else ""
    current_ro = "--read-only" in args

    new_path = st.text_input(
        "Folder path (the only path the agent can read or write within)",
        value=current_path,
        placeholder="/Users/yourname/notes",
        key=f"_mcp_fs_path_{name}",
    )
    new_ro = st.checkbox(
        "Read-only (block write_file and append_file)",
        value=current_ro,
        key=f"_mcp_fs_ro_{name}",
    )

    if st.button("Save", key=f"_mcp_save_fs_{name}"):
        if not new_path.strip():
            st.error("Folder path is required.")
            return
        new_args = ["-m", "return_architecture.mcp_servers.filesystem", new_path.strip()]
        if new_ro:
            new_args.append("--read-only")
        config.setdefault("mcp", {}).setdefault("servers", {})[name] = {
            "command": "python",
            "args":    new_args,
            "env":     {},
        }
        helpers.write_agent_config(slug, config)
        st.success("Saved. Restart the service below to apply.")


def _render_raw_editor(slug: str, config: dict, name: str, entry: dict) -> None:
    command = entry.get("command", "") or ""
    args = list(entry.get("args") or [])
    env = dict(entry.get("env") or {})

    new_command = st.text_input(
        "Command",
        value=command,
        help='Use "python" to run with Return Architecture\'s own Python.',
        key=f"_mcp_cmd_{name}",
    )
    new_args_text = st.text_area(
        "Arguments (one per line)",
        value="\n".join(args),
        height=120,
        key=f"_mcp_args_{name}",
    )

    with st.expander("Environment variables (advanced)", expanded=False):
        env_text_initial = "\n".join(f"{k}={v}" for k, v in env.items())
        new_env_text = st.text_area(
            "One KEY=VALUE per line. Lines starting with # are ignored.",
            value=env_text_initial,
            height=80,
            key=f"_mcp_env_{name}",
        )

    if st.button("Save", key=f"_mcp_save_raw_{name}"):
        new_args = [line.strip() for line in new_args_text.splitlines() if line.strip()]
        new_env = _parse_env(new_env_text)
        config.setdefault("mcp", {}).setdefault("servers", {})[name] = {
            "command": new_command.strip(),
            "args":    new_args,
            "env":     new_env,
        }
        helpers.write_agent_config(slug, config)
        st.success("Saved. Restart the service below to apply.")


def _render_delete_button(slug: str, config: dict, name: str) -> None:
    if st.button("Delete this server", key=f"_mcp_del_{name}"):
        st.session_state[f"_confirm_del_mcp_{name}"] = True
        st.rerun()

    if st.session_state.get(f"_confirm_del_mcp_{name}"):
        st.warning(f"Delete the MCP server **{name}**? This can't be undone.")
        confirm_cols = st.columns([1, 1, 5])
        if confirm_cols[0].button("Yes, delete", key=f"_confirm_del_mcp_yes_{name}"):
            config.get("mcp", {}).get("servers", {}).pop(name, None)
            helpers.write_agent_config(slug, config)
            st.session_state[f"_confirm_del_mcp_{name}"] = False
            st.success(f"Deleted '{name}'.")
            st.rerun()
        if confirm_cols[1].button("Cancel", key=f"_confirm_del_mcp_no_{name}"):
            st.session_state[f"_confirm_del_mcp_{name}"] = False
            st.rerun()


# ── Add: bundled ──────────────────────────────────────────────────────────

def _render_add_bundled(slug: str, config: dict, existing_servers: dict) -> None:
    st.subheader("Add bundled server")

    choice = st.selectbox(
        "Server",
        options=list(BUNDLED_SERVERS.keys()),
        key="_bundled_choice",
    )
    st.caption(BUNDLED_SERVERS[choice]["description"])

    name = st.text_input(
        "Server name",
        value=choice,
        help=(
            "A label for you to recognize this server in your config. "
            "Not used in Telegram and not visible to the agent — the agent "
            "sees the tool names the server exposes (e.g. `fetch_url`). "
            "Lowercase letters, digits, and underscores. Starts with a letter."
        ),
        key="_bundled_name",
    )

    extra_args: list[str] = []

    if choice == "filesystem":
        path = st.text_input(
            "Folder path (the only path the agent can read or write within)",
            value="",
            placeholder="/Users/yourname/notes",
            key="_bundled_fs_path",
        )
        read_only = st.checkbox(
            "Read-only (block write_file and append_file)",
            value=False,
            key="_bundled_fs_ro",
        )
        if path.strip():
            extra_args = [path.strip()]
            if read_only:
                extra_args.append("--read-only")

    cols = st.columns([1, 1, 5])
    if cols[0].button("Create", key="_bundled_create"):
        if not _NAME_RE.match(name or ""):
            st.error("Invalid name. Lowercase letters, digits, underscores; starts with a letter.")
        elif name in existing_servers:
            st.error(f"A server named '{name}' already exists.")
        elif choice == "filesystem" and not extra_args:
            st.error("Folder path is required for the filesystem server.")
        else:
            bundled = BUNDLED_SERVERS[choice]
            full_args = list(bundled["args"]) + extra_args
            config.setdefault("mcp", {}).setdefault("servers", {})[name] = {
                "command": bundled["command"],
                "args":    full_args,
                "env":     {},
            }
            helpers.write_agent_config(slug, config)
            st.session_state["_mcp_add_mode"] = None
            for k in ("_bundled_choice", "_bundled_name", "_bundled_fs_path", "_bundled_fs_ro"):
                st.session_state.pop(k, None)
            st.success(f"Added '{name}'. Restart the service to apply.")
            st.rerun()
    if cols[1].button("Cancel", key="_bundled_cancel"):
        st.session_state["_mcp_add_mode"] = None
        st.rerun()


# ── Add: custom ───────────────────────────────────────────────────────────

def _render_add_custom(slug: str, config: dict, existing_servers: dict) -> None:
    st.subheader("Add custom server")
    st.caption(
        "For an MCP server that isn't bundled with Return Architecture — e.g. "
        "a Node-based server you run via `npx` or a Python server you wrote yourself."
    )

    name = st.text_input(
        "Server name",
        value="",
        help=(
            "A label for you to recognize this server in your config. "
            "Lowercase letters, digits, and underscores. Starts with a letter."
        ),
        key="_custom_name",
    )
    command = st.text_input(
        "Command",
        value="",
        placeholder='e.g. "uvx", "npx", "python"',
        help='Use "python" to run with Return Architecture\'s own Python.',
        key="_custom_cmd",
    )
    args_text = st.text_area(
        "Arguments (one per line)",
        value="",
        height=120,
        placeholder="e.g.\n-m\nreturn_architecture.mcp_servers.url_fetch",
        key="_custom_args",
    )
    with st.expander("Environment variables (advanced)", expanded=False):
        env_text = st.text_area(
            "One KEY=VALUE per line. Lines starting with # are ignored.",
            value="",
            height=80,
            key="_custom_env",
        )

    cols = st.columns([1, 1, 5])
    if cols[0].button("Create", key="_custom_create"):
        if not _NAME_RE.match(name or ""):
            st.error("Invalid name.")
        elif name in existing_servers:
            st.error(f"A server named '{name}' already exists.")
        elif not command.strip():
            st.error("Command is required.")
        else:
            args = [line.strip() for line in args_text.splitlines() if line.strip()]
            env = _parse_env(env_text)
            config.setdefault("mcp", {}).setdefault("servers", {})[name] = {
                "command": command.strip(),
                "args":    args,
                "env":     env,
            }
            helpers.write_agent_config(slug, config)
            st.session_state["_mcp_add_mode"] = None
            for k in ("_custom_name", "_custom_cmd", "_custom_args", "_custom_env"):
                st.session_state.pop(k, None)
            st.success(f"Added '{name}'. Restart the service to apply.")
            st.rerun()
    if cols[1].button("Cancel", key="_custom_cancel"):
        st.session_state["_mcp_add_mode"] = None
        st.rerun()


# ── Helpers ───────────────────────────────────────────────────────────────

def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out
