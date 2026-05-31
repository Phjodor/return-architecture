"""Built-in tools that live inside the runtime.

External tools come in as MCP servers in a later step. Built-in tools are
tightly coupled to the runtime (e.g. no_response, which the loop needs to
recognise to do the right thing) or to the project's core rituals.
"""

from return_architecture.tools.base import Tool, ToolContext, ToolResult
from return_architecture.tools.no_response import NoResponseTool
from return_architecture.tools.send_telegram import SendToHumanTelegramTool
from return_architecture.tools.artifact_tools import (
    ArtifactDeleteReactionTool,
    ArtifactShareMoreTool,
)
from return_architecture.tools.tag_item import TagItemTool
from return_architecture.tools.write_letter import WriteLetterTool
from return_architecture.tools.private_writings import (
    WritePrivatelyTool,
    ListPrivateWritingsTool,
    ReadPrivateWritingTool,
)
from return_architecture.tools.schedule_self import (
    ScheduleSelfTool,
    ListMySchedulesTool,
    CancelMyScheduleTool,
)

BUILTIN_TOOLS: dict[str, Tool] = {
    "no_response": NoResponseTool(),
    "send_to_human_telegram": SendToHumanTelegramTool(),
    "artifact_delete_reaction": ArtifactDeleteReactionTool(),
    "artifact_share_more": ArtifactShareMoreTool(),
    "tag_item": TagItemTool(),
    "write_letter": WriteLetterTool(),
    "write_privately": WritePrivatelyTool(),
    "list_private_writings": ListPrivateWritingsTool(),
    "read_private_writing": ReadPrivateWritingTool(),
    "schedule_self": ScheduleSelfTool(),
    "list_my_schedules": ListMySchedulesTool(),
    "cancel_my_schedule": CancelMyScheduleTool(),
}

__all__ = ["Tool", "ToolContext", "ToolResult", "BUILTIN_TOOLS"]
