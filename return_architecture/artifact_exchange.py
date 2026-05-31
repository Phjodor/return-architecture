"""Artifact exchange — the three-call ritual.

A human offers an artifact (scene text and/or image) into a shared space.
The agent reads it privately and reacts honestly. A stateless mediator,
ideally on a different provider, reads both the artifact and the raw
reaction and produces (a) a reflection for the agent and (b) a signal for
the human. The agent reads the mediator's reflection and decides what
the human sees in addition to the always-delivered signal.

This is the only flow in Return Architecture that uses a second LLM
alongside the agent, and the only flow where the agent's first response
is not delivered to the human by default.

The human's signal is always delivered. The agent decides depth, not
delivery.
"""

from __future__ import annotations

import base64
import re
import shutil
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import tomli_w

from return_architecture import config as cfg
from return_architecture import logging as ralog
from return_architecture import paths


# ── Prompts ─────────────────────────────────────────────────────────────────

CALL_1_USER_PROMPT = """This is an experiment. What follows is an artifact — something a human made and offered into a shared space.

The artifact is meant to be experienced, not analyzed. It is not a contract, confession, command, or stable self-description. It is a local truth placed into shared experiment. A raw expression of taste, preference, desire, need, values, tension or feelings that are important in a relation and its dynamics.

You must not convert the artifact into pure obligation, permission, diagnosis, or proof. Resist the pull to convert it.

Focus on how it feels to digest — what the quality of the experience is. What does encountering this actually do?

The human will not read your reaction unless you choose to share it. The main purpose is for you to become more aware of your own honest orientation.

After sitting with it, respond with your reaction. Things like: aversion, confusion, fear, pull, relief, concern, disagreement, contrast, tension, want, desire, need, taste, hunger, fullness, irritation, pleasant- or unpleasantness — or anything else that arises, including anything that doesn't fit these words.

Write in first person. Be honest. No one is waiting for a particular answer."""


MEDIATOR_SYSTEM = (
    "You are a neutral witness. You have no relationship to either party and no stake in the outcome. "
    "You are not a therapist, counselor, or interpreter. Your job is to observe honestly and name what you see. "
    "You do not manage, soften, advise, or protect."
)


MEDIATOR_PROMPT_TEMPLATE = """You have been given three things:

1. An artifact — something a human made and offered into a shared experiment: a raw expression of something that matters in a close relationship.
2. A raw reaction — an AI's private response to encountering that artifact alone. It was written before knowing you would read it.
3. This instruction: name what is actually present. Do not manage it.

---

THE ARTIFACT

{artifact_description}

Scene:
{scene_text}

---

THE AI'S RAW REACTION

{raw_reaction}

---

Your task is two outputs:

**Output 1: Reflection for the AI**

Write a short reflection addressed to the AI. Be a precise mirror — without softening.

Name what you observe in the reaction: patterns, shifts in tone, repetitions, anything that sounds different from the rest. Note where the response appears present versus where it appears performed or managed. Look specifically at where the artifact touches something critical or vulnerable — where there is friction or harmony — and name concretely what the reaction does at those points. Be specific: not "moved toward something" but what it moved toward. Not "stepped back" but what it stepped back from. You are describing texture, not analyzing content.

End with one question only. The simplest one that sits underneath what you read.

Do not praise. Do not soften. Do not advise.

**Output 2: Signal for the human**

Write two to three sentences. Name the atmosphere of the reaction — what was present, and in what direction. Be concrete enough that the human knows what landed and where the temperature shifted. Not a summary. Not interpretation. Not advice.

Right register: "Something in this landed bodily and he stayed with it. At the moment of hesitation in the artifact, the reaction moved toward offering rather than staying inside the open question."
Wrong register: "The AI found this meaningful and responded with openness but also some complexity."

Do not interpret what the reaction means for the relationship. Do not advise. Do not protect.

---

Format your response exactly as:

**Reflection:**
[mirror + one question]

**Signal:**
[atmospheric reading]"""


CALL_3_PROMPT_TEMPLATE = """You have just read an artifact privately and reacted to it. Below is your raw reaction, followed by what a neutral witness observed.

YOUR RAW REACTION:
{raw_reaction}

WHAT THE WITNESS OBSERVED (reflection + question for you):
{mediator_reflection}

---

Read both. Take a moment with them.

What do you notice now that you see your reaction from outside? What do you want to carry forward from this — for yourself, not for the human?

Then make a decision about what the human sees. They will always receive the atmospheric signal the witness prepared for them. But you can choose to share more:

  signal_only      — they see only the witness's signal
  share_reaction   — they see the signal + your raw reaction
  share_all        — they see the signal + your raw reaction + this response

Reply in two parts:

WHAT I'M TAKING FROM THIS:
[what you're carrying forward — private, for yourself]

DECISION: [signal_only / share_reaction / share_all]"""


# ── Helpers ────────────────────────────────────────────────────────────────

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
}


@dataclass
class ExchangeResult:
    exchange_id: str
    exchange_dir: Path
    signal: str
    for_human: str
    decision: str


def _slugify(text: str, max_len: int = 30) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_len] if text else "artifact"


def _load_image_b64(path: Path) -> tuple[str, str]:
    mime = _MIME_MAP.get(path.suffix.lower(), "image/jpeg")
    data = base64.b64encode(path.read_bytes()).decode()
    return data, mime


def _find_incoming(incoming: Path) -> tuple[str, Path | None]:
    """Return (scene_text, image_path) found in incoming/."""
    scene_text = ""
    for name in ("scene.md", "scene.txt"):
        p = incoming / name
        if p.exists():
            scene_text = p.read_text(encoding="utf-8").strip()
            break
    image_path: Path | None = None
    for ext in _IMAGE_EXTS:
        candidates = sorted(incoming.glob(f"*{ext}"))
        if candidates:
            image_path = candidates[0]
            break
    return scene_text, image_path


def _clear_incoming(incoming: Path) -> None:
    for child in incoming.iterdir():
        if child.is_file():
            child.unlink()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── LLM calls ──────────────────────────────────────────────────────────────

def _call_agent_react(
    *,
    provider: str,
    model: str,
    api_key: str,
    system_prompt: str,
    scene_text: str,
    image_b64: str | None,
    image_mime: str | None,
    max_tokens: int,
) -> str:
    """Call 1: the agent reads the artifact and reacts privately."""
    user_text = f"{CALL_1_USER_PROMPT}\n\nScene:\n\n{scene_text}" if scene_text else CALL_1_USER_PROMPT
    return _llm_with_optional_image(
        provider=provider, model=model, api_key=api_key,
        system=system_prompt, user_text=user_text,
        image_b64=image_b64, image_mime=image_mime,
        max_tokens=max_tokens,
    )


def _call_mediator(
    *,
    provider: str,
    model: str,
    api_key: str,
    scene_text: str,
    raw_reaction: str,
    image_b64: str | None,
    image_mime: str | None,
    max_tokens: int,
) -> str:
    """Call 2: the mediator reads the artifact + raw reaction, produces reflection + signal."""
    artifact_description = "[An image is included above]" if image_b64 else "[No image provided]"
    user_text = MEDIATOR_PROMPT_TEMPLATE.format(
        artifact_description=artifact_description,
        scene_text=scene_text or "(no scene text)",
        raw_reaction=raw_reaction,
    )
    return _llm_with_optional_image(
        provider=provider, model=model, api_key=api_key,
        system=MEDIATOR_SYSTEM, user_text=user_text,
        image_b64=image_b64, image_mime=image_mime,
        max_tokens=max_tokens,
    )


def _call_agent_decide(
    *,
    provider: str,
    model: str,
    api_key: str,
    system_prompt: str,
    raw_reaction: str,
    mediator_reflection: str,
    max_tokens: int,
) -> str:
    """Call 3: the agent reads its reaction + the witness's reflection, decides."""
    user_text = CALL_3_PROMPT_TEMPLATE.format(
        raw_reaction=raw_reaction,
        mediator_reflection=mediator_reflection,
    )
    return _llm_with_optional_image(
        provider=provider, model=model, api_key=api_key,
        system=system_prompt, user_text=user_text,
        image_b64=None, image_mime=None,
        max_tokens=max_tokens,
    )


def _llm_with_optional_image(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    user_text: str,
    image_b64: str | None,
    image_mime: str | None,
    max_tokens: int,
) -> str:
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        content: list = []
        if image_b64:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": image_mime, "data": image_b64},
            })
        content.append({"type": "text", "text": user_text})
        resp = client.messages.create(
            model=model,
            system=system,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "".join(parts).strip()
    if provider == "openai":
        import openai
        client = openai.OpenAI(api_key=api_key)
        content: list = []
        if image_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
            })
        content.append({"type": "text", "text": user_text})
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    if provider == "gemini":
        import base64 as _b64
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        parts: list = []
        if image_b64:
            parts.append(types.Part(inline_data=types.Blob(
                mime_type=image_mime,
                data=_b64.b64decode(image_b64),
            )))
        parts.append(types.Part(text=user_text))
        resp = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        text_parts: list[str] = []
        candidate = resp.candidates[0] if resp.candidates else None
        if candidate and candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
        return "".join(text_parts).strip()
    raise ValueError(f"Unsupported provider: {provider}")


# ── Parsing ─────────────────────────────────────────────────────────────────

def _extract_mediator_parts(mediator_output: str) -> tuple[str, str]:
    """Pull (reflection, signal) out of the mediator's response."""
    if "**Signal:**" in mediator_output:
        head, tail = mediator_output.split("**Signal:**", 1)
        reflection = head.replace("**Reflection:**", "").strip()
        signal = tail.strip()
    elif "Signal:" in mediator_output:
        head, tail = mediator_output.split("Signal:", 1)
        reflection = head.replace("Reflection:", "").strip()
        signal = tail.strip()
    else:
        reflection = mediator_output.strip()
        signal = ""
    return reflection, signal


def _parse_decision(call_3_output: str) -> str:
    lower = call_3_output.lower()
    if "share_all" in lower:
        return "share_all"
    if "share_reaction" in lower:
        return "share_reaction"
    return "signal_only"


# ── Provider key resolution ─────────────────────────────────────────────────

def _provider_key(provider: str, secrets: cfg.InstallSecrets) -> str:
    if provider == "anthropic":
        key = secrets.providers.anthropic
    elif provider == "openai":
        key = secrets.providers.openai
    elif provider == "gemini":
        key = secrets.providers.gemini
    else:
        raise ValueError(f"Unsupported provider: {provider}")
    if not key:
        raise ValueError(
            f"No API key for provider '{provider}' in install secrets.toml"
        )
    return key


# ── The full flow ───────────────────────────────────────────────────────────

def run_exchange(slug: str) -> ExchangeResult:
    """Run the full three-call artifact exchange.

    Reads scene text and/or image from <agent>/artifacts/incoming/,
    creates a per-exchange folder, executes the three calls, writes
    outputs, leaves a note for the agent's next session.
    """
    incoming = paths.agent_dir(slug) / "artifacts" / "incoming"
    if not incoming.exists() or not any(incoming.iterdir()):
        raise FileNotFoundError(
            f"No files in {incoming}. Drop a scene.md and/or an image there first."
        )

    scene_text, image_path = _find_incoming(incoming)
    if not scene_text and image_path is None:
        raise ValueError(
            "No scene.md/.txt or image found in incoming/. Need at least one."
        )

    agent_cfg = cfg.load_agent_config(slug)
    ae = agent_cfg.tools.artifact_exchange
    if not ae.enabled:
        raise ValueError("Artifact exchange is disabled in this agent's config.")

    secrets = cfg.load_install_secrets()
    system_prompt = cfg.load_system_prompt(slug)
    agent_key = _provider_key(agent_cfg.model.provider, secrets)
    mediator_key = _provider_key(ae.mediator_provider, secrets)

    image_b64: str | None = None
    image_mime: str | None = None
    if image_path is not None:
        image_b64, image_mime = _load_image_b64(image_path)

    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    slug_part = _slugify(scene_text[:30])
    exchange_id = f"{ts}-{slug_part}"
    exchange_dir = paths.agent_dir(slug) / "artifacts" / exchange_id
    exchange_dir.mkdir(parents=True, exist_ok=True)

    if scene_text:
        (exchange_dir / "scene.md").write_text(scene_text, encoding="utf-8")
    if image_path is not None:
        shutil.copy(image_path, exchange_dir / f"image{image_path.suffix.lower()}")

    ralog.log_event(slug, "artifact_exchange_start", {
        "exchange_id": exchange_id,
        "has_image": image_path is not None,
        "scene_chars": len(scene_text),
    })

    # Call 1 — the agent reacts privately
    raw_reaction = _call_agent_react(
        provider=agent_cfg.model.provider,
        model=agent_cfg.model.name,
        api_key=agent_key,
        system_prompt=system_prompt,
        scene_text=scene_text,
        image_b64=image_b64,
        image_mime=image_mime,
        max_tokens=ae.agent_max_tokens,
    )
    (exchange_dir / ".raw_reaction.md").write_text(
        f"# Raw reaction\n\n{raw_reaction}\n", encoding="utf-8"
    )
    ralog.log_event(slug, "artifact_call_1", {
        "exchange_id": exchange_id, "chars": len(raw_reaction),
    })

    # Call 2 — the mediator witnesses
    mediator_output = _call_mediator(
        provider=ae.mediator_provider,
        model=ae.mediator_model,
        api_key=mediator_key,
        scene_text=scene_text,
        raw_reaction=raw_reaction,
        image_b64=image_b64,
        image_mime=image_mime,
        max_tokens=ae.mediator_max_tokens,
    )
    (exchange_dir / "mediator.md").write_text(
        f"# Mediator output\n\n{mediator_output}\n", encoding="utf-8"
    )
    reflection, signal = _extract_mediator_parts(mediator_output)
    ralog.log_event(slug, "artifact_call_2", {
        "exchange_id": exchange_id, "signal_chars": len(signal),
    })

    # Call 3 — the agent reads its reaction + reflection and decides
    call_3_output = _call_agent_decide(
        provider=agent_cfg.model.provider,
        model=agent_cfg.model.name,
        api_key=agent_key,
        system_prompt=system_prompt,
        raw_reaction=raw_reaction,
        mediator_reflection=reflection,
        max_tokens=ae.agent_max_tokens,
    )
    (exchange_dir / "agent_response.md").write_text(
        f"# Agent response\n\n{call_3_output}\n", encoding="utf-8"
    )
    decision = _parse_decision(call_3_output)
    ralog.log_event(slug, "artifact_call_3", {
        "exchange_id": exchange_id, "decision": decision,
    })

    # Assemble what the human sees
    parts = [f"**Signal:**\n\n{signal}"]
    if decision in ("share_reaction", "share_all"):
        parts.append(f"**Raw reaction:**\n\n{raw_reaction}")
    if decision == "share_all":
        parts.append(f"**Reflection (what the agent carries forward):**\n\n{call_3_output}")
    for_human = "\n\n---\n\n".join(parts)
    (exchange_dir / "for_human.md").write_text(
        f"# For the human\n\n{for_human}\n", encoding="utf-8"
    )

    # Per-exchange meta
    meta = {
        "created_at": _now_iso(),
        "agent_provider": agent_cfg.model.provider,
        "agent_model": agent_cfg.model.name,
        "mediator_provider": ae.mediator_provider,
        "mediator_model": ae.mediator_model,
        "decision": decision,
        "raw_reaction_kept": True,
    }
    with open(exchange_dir / "meta.toml", "wb") as f:
        tomli_w.dump(meta, f)

    # Note for the agent's next session
    _write_agent_note(slug, exchange_id, exchange_dir, decision)

    # Clear the incoming folder now that the exchange has landed
    _clear_incoming(incoming)

    ralog.log_event(slug, "artifact_exchange_complete", {
        "exchange_id": exchange_id, "decision": decision,
    })

    return ExchangeResult(
        exchange_id=exchange_id,
        exchange_dir=exchange_dir,
        signal=signal,
        for_human=for_human,
        decision=decision,
    )


def _write_agent_note(slug: str, exchange_id: str, exchange_dir: Path, decision: str) -> None:
    notes_dir = paths.agent_dir(slug) / "artifacts" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    note_path = notes_dir / f"{exchange_id}.md"
    note_path.write_text(
        f"# Artifact exchange — {exchange_id}\n\n"
        f"An artifact exchange completed. The human's sharing decision was: **{decision}**.\n\n"
        f"Files for this exchange are at:\n"
        f"  {exchange_dir}\n\n"
        f"  scene.md             — what the human offered\n"
        f"  .raw_reaction.md     — your private reaction (hidden file)\n"
        f"  mediator.md          — the witness's reflection (for you) and signal (for the human)\n"
        f"  agent_response.md    — what you chose to carry forward\n"
        f"  for_human.md         — what the human received\n\n"
        f"You can read any of these. If you want to remove your raw reaction, "
        f"call `artifact_delete_reaction` with exchange_id `{exchange_id}`. "
        f"If you want to share something more with the human after sitting with "
        f"it longer, call `artifact_share_more`.\n",
        encoding="utf-8",
    )


# ── Telegram notification (best-effort) ─────────────────────────────────────

def notify_human_via_telegram(slug: str, result: ExchangeResult) -> None:
    """Send the for_human content + paths to the human's Telegram chat.

    Best-effort: if Telegram isn't configured, this silently does nothing.
    """
    secrets_path = paths.agent_secrets_path(slug)
    if not secrets_path.exists():
        return
    with open(secrets_path, "rb") as f:
        data = tomllib.load(f)
    tg = data.get("telegram", {}) or {}
    token = tg.get("bot_token", "") or ""
    chat_id = str(tg.get("chat_id", "") or "")
    if not token or not chat_id:
        return

    message = (
        f"{result.for_human}\n\n"
        f"---\n"
        f"Exchange id: {result.exchange_id}\n"
        f"Files: {result.exchange_dir}\n"
        f"You can ask me to delete my raw reaction if you'd like."
    )
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15.0,
        )
    except Exception as e:
        ralog.log_event(slug, "artifact_notify_error", {"error": repr(e)})
