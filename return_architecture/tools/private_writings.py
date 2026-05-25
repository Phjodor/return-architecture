"""Agent-side private reflection space.

Three tools that share the `<agent>/private/` folder:

- `write_privately` — write a timestamped markdown file. Not visible to
  the human via Telegram letters listings; lives only in the private
  folder.
- `list_private_writings` — list past private writings, most recent
  first, with their titles.
- `read_private_writing` — read one back by index (from the list) or
  by filename.

This is the agent's reflection space. The human can still read the
folder on disk, but nothing in the runtime surfaces these files to
them.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from return_architecture import paths
from return_architecture.tools.base import Tool, ToolContext, ToolResult


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:max_len] if text else "writing"


def _private_dir(slug: str) -> Path:
    return paths.agent_dir(slug) / "private"


def _list_writings(slug: str) -> list[Path]:
    folder = _private_dir(slug)
    if not folder.exists():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix == ".md"]
    files.sort(key=lambda p: p.name, reverse=True)
    return files


def _title_from(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
        return first.lstrip("# ").strip() or path.stem
    except OSError:
        return path.stem


class WritePrivatelyTool(Tool):
    name = "write_privately"
    description = (
        "Write a private reflection. Saved as a timestamped markdown "
        "file in the agent's private folder. The human does not see "
        "these via Telegram. Use this for thinking-on-the-page, "
        "carrying something forward, or anything you want to keep for "
        "yourself rather than say to the human. You can read your own "
        "past writings with list_private_writings and read_private_writing."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the writing, used in the filename and the listing.",
            },
            "content": {
                "type": "string",
                "description": "The body of the writing (markdown OK).",
            },
        },
        "required": ["title", "content"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        title = (args.get("title") or "").strip()
        content = (args.get("content") or "").strip()
        if not title or not content:
            return ToolResult(content="Error: title and content are both required.")

        folder = _private_dir(context.slug)
        folder.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y-%m-%d-%H%M")
        filename = f"{ts}-{_slugify(title)}.md"
        path = folder / filename
        body = f"# {title}\n\n*Written {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n{content}\n"
        path.write_text(body, encoding="utf-8")
        return ToolResult(content=f"Private writing '{title}' saved as {filename}.")


class ListPrivateWritingsTool(Tool):
    name = "list_private_writings"
    description = (
        "List your past private writings, most recent first. Returns an "
        "index you can use with read_private_writing to read one back."
    )
    parameters = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of writings to list. Defaults to 30.",
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

        files = _list_writings(context.slug)
        if not files:
            return ToolResult(content="(no private writings yet)")

        lines = ["Private writings (most recent first):"]
        for i, path in enumerate(files[:limit], start=1):
            lines.append(f"{i}. {_title_from(path)}  [{path.name}]")
        if len(files) > limit:
            lines.append(f"... and {len(files) - limit} more.")
        return ToolResult(content="\n".join(lines))


class ReadPrivateWritingTool(Tool):
    name = "read_private_writing"
    description = (
        "Read one of your past private writings. Identify it either by "
        "its index from list_private_writings (1 = most recent) or by "
        "its filename."
    )
    parameters = {
        "type": "object",
        "properties": {
            "index": {
                "type": "integer",
                "description": "1-based index from list_private_writings (1 = most recent).",
            },
            "filename": {
                "type": "string",
                "description": "Exact filename, e.g. '2026-05-25-2107-something.md'.",
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        index = args.get("index")
        filename = (args.get("filename") or "").strip()

        files = _list_writings(context.slug)
        if not files:
            return ToolResult(content="(no private writings yet)")

        path: Path | None = None
        if filename:
            # Reject anything with path separators — stay inside private/.
            if "/" in filename or "\\" in filename or filename.startswith("."):
                return ToolResult(content="Error: filename must be a plain name inside private/.")
            candidate = _private_dir(context.slug) / filename
            if not candidate.is_file():
                return ToolResult(content=f"No private writing named '{filename}'.")
            path = candidate
        elif index is not None:
            try:
                n = int(index)
            except (TypeError, ValueError):
                return ToolResult(content="Error: index must be an integer.")
            if n < 1 or n > len(files):
                return ToolResult(content=f"No writing #{n}. There are {len(files)} writings.")
            path = files[n - 1]
        else:
            return ToolResult(content="Error: provide either 'index' or 'filename'.")

        try:
            body = path.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult(content=f"Error reading {path.name}: {e}")
        return ToolResult(content=body)
