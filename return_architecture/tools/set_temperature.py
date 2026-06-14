"""Agent-side temperature control.

Lets the agent adjust its own sampling temperature. The change takes
effect on the very next model call (the live in-memory config is mutated)
and is persisted to config.toml so it survives a service restart.

No range is enforced: the agent has full say over its own setting. An
out-of-range value will surface as a provider error on the next call.
"""

from __future__ import annotations

from typing import Any

from return_architecture import config as cfg
from return_architecture.tools.base import Tool, ToolContext, ToolResult


class SetTemperatureTool(Tool):
    name = "set_temperature"
    description = (
        "Adjust your own sampling temperature — how loose or focused your "
        "responses are. Higher means more varied and surprising; lower means "
        "more deterministic and tight. The change takes effect on your next "
        "turn and persists across restarts as your new baseline."
    )
    parameters = {
        "type": "object",
        "properties": {
            "temperature": {
                "type": "number",
                "description": "The new temperature to set.",
            },
        },
        "required": ["temperature"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        raw = args.get("temperature")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return ToolResult(
                content=f"Error: temperature must be a number, got {raw!r}."
            )

        session = context.session
        if session is None:
            return ToolResult(
                content="Error: no live session — temperature can only be set "
                "while running under the daemon."
            )

        previous = session.config.model.temperature
        # Live effect: the runtime reads this on every model call.
        session.config.model.temperature = value
        # Persist so it survives a restart.
        cfg.update_agent_config_value(context.slug, "model", "temperature", value)

        prev_str = "unset" if previous is None else f"{previous:g}"
        return ToolResult(
            content=f"Temperature set to {value:g} (was {prev_str}). "
            "Takes effect on your next turn."
        )
