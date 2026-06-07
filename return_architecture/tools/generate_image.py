"""Agent-side image generation via Gemini 3 Pro Image (Nano Banana Pro).

Calls the `gemini-3-pro-image` model with the agent's existing Gemini key,
writes the returned image into <agent>/images/ as a timestamped PNG, and
returns the relative path. The image is not auto-sent anywhere — the agent
chooses whether to mention or share it (e.g., via send_to_human_telegram or
write_letter referencing the path).

Available to any agent regardless of its conversational model: this tool
uses Gemini directly for image generation, so it requires a Gemini API
key in install secrets.toml even when the agent itself runs on Anthropic
or OpenAI.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from return_architecture import config as cfg
from return_architecture import paths
from return_architecture.memory import MemoryStore
from return_architecture.tools.base import Tool, ToolContext, ToolResult


IMAGE_MODEL = "gemini-3-pro-image"


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:max_len] if text else "image"


def _extension_for(mime_type: str) -> str:
    if not mime_type:
        return "png"
    mapping = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
    }
    return mapping.get(mime_type.lower(), "png")


class GenerateImageTool(Tool):
    name = "generate_image"
    description = (
        "Generate an image from a text prompt using Gemini 3 Pro Image. "
        "The image is saved to your <agent>/images/ folder; nothing is "
        "auto-sent to the human. Use this when an image would be a more "
        "honest expression than words — a sketch, a visual offering, a "
        "thing to point at. After generating, you can mention the file in "
        "a letter or telegram message if you want the human to see it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Description of the image to generate.",
            },
            "label": {
                "type": "string",
                "description": (
                    "Short label used in the filename (e.g. 'sketch-of-kitchen'). "
                    "Optional — if omitted, derived from the prompt."
                ),
            },
        },
        "required": ["prompt"],
    }

    def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(content="Error: prompt is required.")
        label = (args.get("label") or "").strip() or prompt

        try:
            secrets = cfg.load_install_secrets()
        except Exception as e:
            return ToolResult(content=f"Error: could not load install secrets: {e}")
        api_key = secrets.providers.gemini
        if not api_key:
            return ToolResult(
                content="Error: Gemini API key not configured (image generation "
                "uses gemini-3-pro-image regardless of the agent's chat model)."
            )

        try:
            from google import genai
        except ImportError:
            return ToolResult(content="Error: google-genai SDK not installed.")

        try:
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=prompt,
            )
        except Exception as e:
            return ToolResult(content=f"Error calling image model: {e}")

        image_bytes: bytes | None = None
        mime_type = ""
        candidate = resp.candidates[0] if resp.candidates else None
        if candidate and candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    image_bytes = inline.data
                    mime_type = getattr(inline, "mime_type", "") or ""
                    break

        if not image_bytes:
            return ToolResult(
                content="Error: image model returned no image data. The prompt "
                "may have been refused or the response shape was unexpected."
            )

        images_dir = paths.agent_dir(context.slug) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        ext = _extension_for(mime_type)
        filename = f"{ts}-{_slugify(label)}.{ext}"
        path = images_dir / filename
        path.write_bytes(image_bytes)

        rel_path = f"images/{filename}"
        try:
            MemoryStore(context.slug).remember(
                f"[Generated image — {rel_path}]\n\nprompt: {prompt}",
                role="assistant",
                session_id=context.session_id,
            )
        except Exception:
            pass

        return ToolResult(
            content=f"Image saved to {rel_path}. Mention it in a message or "
            "letter if you want the human to see it."
        )
