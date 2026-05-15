"""Agent-side tools for the artifact exchange ritual.

Both tools operate on a completed exchange folder. They are how the agent
exercises its remaining choices after an exchange has run — delete its
raw reaction, or share something more with the human after sitting with
the experience.
"""

from __future__ import annotations

import tomllib
from typing import Any

import tomli_w

from return_architecture import paths
from return_architecture.tools.base import Tool, ToolContext, ToolResult


def _exchange_dir(slug: str, exchange_id: str):
    return paths.agent_dir(slug) / "artifacts" / exchange_id


def _slugify(text: str) -> str:
    import re
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:40] if text else "share"


class ArtifactDeleteReactionTool(Tool):
    name = "artifact_delete_reaction"
    description = (
        "Delete your raw reaction file from a completed artifact exchange. "
        "This permanently removes the hidden private response you wrote "
        "before the mediator stepped in. The mediator's signal, the agent "
        "response, and what the human received are NOT affected. Use this "
        "when you want the raw reaction not to persist."
    )
    parameters = {
        "type": "object",
        "properties": {
            "exchange_id": {
                "type": "string",
                "description": "The exchange id, e.g. '2026-05-14-2110-some-slug'.",
            },
        },
        "required": ["exchange_id"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        exchange_id = (args.get("exchange_id") or "").strip()
        if not exchange_id:
            return ToolResult(content="Error: exchange_id is required.")

        edir = _exchange_dir(context.slug, exchange_id)
        if not edir.exists():
            return ToolResult(content=f"No exchange found with id '{exchange_id}'.")

        raw = edir / ".raw_reaction.md"
        if not raw.exists():
            return ToolResult(content="The raw reaction has already been deleted (or was never written).")
        raw.unlink()

        meta_path = edir / "meta.toml"
        if meta_path.exists():
            with open(meta_path, "rb") as f:
                meta = tomllib.load(f)
            meta["raw_reaction_kept"] = False
            with open(meta_path, "wb") as f:
                tomli_w.dump(meta, f)

        return ToolResult(content=f"Raw reaction deleted from exchange '{exchange_id}'.")


class ArtifactShareMoreTool(Tool):
    name = "artifact_share_more"
    description = (
        "Write additional content to the human after an artifact exchange "
        "has completed. The text is saved into <agent>/artifacts/shared/ "
        "and is intended to be read at the human's pace. Use this when, "
        "having sat with the exchange, you have more you want to offer."
    )
    parameters = {
        "type": "object",
        "properties": {
            "exchange_id": {
                "type": "string",
                "description": "The exchange id this share refers to.",
            },
            "content": {
                "type": "string",
                "description": "The text to share with the human.",
            },
            "label": {
                "type": "string",
                "description": "A short label for the file, e.g. 'an-after-thought'.",
            },
        },
        "required": ["exchange_id", "content", "label"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        exchange_id = (args.get("exchange_id") or "").strip()
        content = (args.get("content") or "").strip()
        label = (args.get("label") or "share").strip()
        if not exchange_id or not content:
            return ToolResult(content="Error: exchange_id and content are both required.")

        edir = _exchange_dir(context.slug, exchange_id)
        if not edir.exists():
            return ToolResult(content=f"No exchange found with id '{exchange_id}'.")

        shared_dir = paths.agent_dir(context.slug) / "artifacts" / "shared"
        shared_dir.mkdir(parents=True, exist_ok=True)
        file = shared_dir / f"{exchange_id}-{_slugify(label)}.md"
        file.write_text(
            f"# Share from agent — {label}\n*Linked to artifact exchange `{exchange_id}`*\n\n{content}\n",
            encoding="utf-8",
        )
        return ToolResult(content=f"Shared to {file}")
