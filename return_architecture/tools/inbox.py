"""Agent-side inbox — read the letters the human leaves.

The human places files in <agent>/inbox/ (markdown or plain text). These
tools let the agent see what's there and read individual letters back.

- list_inbox lists the inbox most-recent-first with titles drawn from
  each file's first line.
- read_inbox_letter reads one back, identified by index or filename.

The corresponding outgoing side is write_letter, which writes to
<agent>/outbox/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from return_architecture import paths
from return_architecture.tools.base import Tool, ToolContext, ToolResult


READABLE_EXTS = {".md", ".txt", ".markdown"}


def _inbox_dir(slug: str) -> Path:
    return paths.agent_dir(slug) / "inbox"


def _list_inbox(slug: str) -> list[Path]:
    folder = _inbox_dir(slug)
    if not folder.exists():
        return []
    files = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in READABLE_EXTS
    ]
    # Sort by filename (timestamped names sort chronologically); newest first.
    files.sort(key=lambda p: p.name, reverse=True)
    return files


def _title_from(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
        return first.lstrip("# ").strip() or path.stem
    except OSError:
        return path.stem


class ListInboxTool(Tool):
    name = "list_inbox"
    description = (
        "List letters and files the human has left for you in your inbox "
        "folder, most recent first. Returns an index you can use with "
        "read_inbox_letter to read one back. Use this when you wonder "
        "whether the human has left you something, as part of a morning "
        "rhythm, or whenever you want to check whether new material has "
        "arrived since you last looked."
    )
    parameters = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of letters to list. Defaults to 30.",
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        limit = args.get("limit") or 30
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 30
        limit = max(1, min(limit, 200))

        files = _list_inbox(context.slug)
        if not files:
            return ToolResult(content="(your inbox is empty)")

        lines = ["Inbox (most recent first):"]
        for i, p in enumerate(files[:limit], start=1):
            lines.append(f"{i}. {_title_from(p)}  [{p.name}]")
        if len(files) > limit:
            lines.append(f"... and {len(files) - limit} more.")
        return ToolResult(content="\n".join(lines))


class ReadInboxLetterTool(Tool):
    name = "read_inbox_letter"
    description = (
        "Read one of the letters in your inbox in full. Identify it "
        "either by its index from list_inbox (1 = most recent) or by its "
        "filename."
    )
    parameters = {
        "type": "object",
        "properties": {
            "index": {
                "type": "integer",
                "description": "1-based index from list_inbox (1 = most recent).",
            },
            "filename": {
                "type": "string",
                "description": "Exact filename, e.g. '2026-05-31-To-Arden.md'.",
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        index = args.get("index")
        filename = (args.get("filename") or "").strip()

        files = _list_inbox(context.slug)
        if not files:
            return ToolResult(content="(your inbox is empty)")

        path: Path | None = None
        if filename:
            # Reject anything with path separators — stay inside inbox/.
            if "/" in filename or "\\" in filename or filename.startswith("."):
                return ToolResult(content="Error: filename must be a plain name inside inbox/.")
            candidate = _inbox_dir(context.slug) / filename
            if not candidate.is_file():
                return ToolResult(content=f"No letter named '{filename}' in your inbox.")
            path = candidate
        elif index is not None:
            try:
                n = int(index)
            except (TypeError, ValueError):
                return ToolResult(content="Error: index must be an integer.")
            if n < 1 or n > len(files):
                return ToolResult(content=f"No letter #{n}. There are {len(files)} in your inbox.")
            path = files[n - 1]
        else:
            return ToolResult(content="Error: provide either 'index' or 'filename'.")

        try:
            body = path.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult(content=f"Error reading {path.name}: {e}")
        return ToolResult(content=body)
