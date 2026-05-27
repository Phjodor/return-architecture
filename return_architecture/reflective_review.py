"""Reflective Interruption Layer — periodic, low-authority pattern review.

Every so often (14 days *or* 300 messages since the last run), a stateless
analyzer reads recent context — conversation including the Telegram chat,
items, open commitments — and writes a single letter addressed in two
halves (one to the human, one to the agent), which both are meant to read
in full. A short summary is sent to Telegram and pinned into the agent's
context, pointing at the letter on disk.

The analyzer runs on a *different model* than the agent and is given only
what it is shown — no agent memory, no agent identity, no scoring, no
verdicts. Its purpose is to introduce reflection and friction before
patterns become invisible structure, not to decide what is true. Every
line it writes is contestable.

The detection targets: assumption hardening, frame narrowing, charged-term
buildup, burden drift, recurring unresolved tension.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from return_architecture import config as cfg
from return_architecture import logging as ralog
from return_architecture import memory as ramem
from return_architecture import paths
from return_architecture import summaries as ra_summaries


# ── Principles (the rules the analyzer works under) ─────────────────────────

DEFAULT_PRINCIPLES = """# Reflection principles

The analyzer works under these rules:

- Do not confuse repetition with truth.
- Distinguish observation, interpretation, and conviction.
- Notice when tentative frames become ambient assumptions.
- Track asymmetries of burden.
- Preserve uncertainty where warranted.
- Do not pathologize intimacy or conflict by default.
- Flag patterns without issuing verdicts.
"""


def _principles_path(slug: str) -> Path:
    return paths.agent_dir(slug) / "reflection_principles.md"


def load_principles(slug: str) -> str:
    """Return the agent's principles file, writing the default if absent.

    Kept on disk and editable so the lens can be tuned without code changes.
    """
    path = _principles_path(slug)
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_PRINCIPLES, encoding="utf-8")
        except OSError:
            return DEFAULT_PRINCIPLES
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_PRINCIPLES


# ── Run state (for the trigger) ─────────────────────────────────────────────

def _reflections_dir(slug: str) -> Path:
    return paths.agent_dir(slug) / "reflections"


def _state_path(slug: str) -> Path:
    return _reflections_dir(slug) / ".state.json"


def _load_state(slug: str) -> dict:
    path = _state_path(slug)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(slug: str, state: dict) -> None:
    path = _state_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _messages_since(slug: str, since_iso: str) -> int:
    """Count turn events (human + agent messages) logged since a timestamp."""
    logs_dir = paths.agent_logs_dir(slug)
    if not logs_dir.exists():
        return 0
    count = 0
    for path in sorted(logs_dir.glob("conversations-*.ndjson")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") not in ("user_message", "assistant_message"):
                        continue
                    if (ev.get("ts") or "") >= since_iso:
                        count += 1
        except OSError:
            continue
    return count


def should_run(slug: str) -> tuple[bool, str]:
    """Decide whether the trigger conditions are met. Returns (run, reason)."""
    agent_cfg = cfg.load_agent_config(slug)
    rr = agent_cfg.reflective_review
    state = _load_state(slug)
    last_run = state.get("last_run")
    if not last_run:
        return True, "first run (no prior reflection)"

    try:
        last_dt = datetime.fromisoformat(last_run)
    except ValueError:
        return True, "unparseable last_run; running"

    days = (datetime.now(timezone.utc) - last_dt).days
    if days >= rr.threshold_days:
        return True, f"{days}d since last run (≥ {rr.threshold_days}d)"
    msgs = _messages_since(slug, last_run)
    if msgs >= rr.threshold_messages:
        return True, f"{msgs} messages since last run (≥ {rr.threshold_messages})"
    return (
        False,
        f"not due: {days}d / {msgs} msgs since last run "
        f"(thresholds {rr.threshold_days}d / {rr.threshold_messages} msgs)",
    )


# ── Stateless analyzer call (different model, tools-required) ────────────────

ANALYZER_SYSTEM_TEMPLATE = """You are an independent, stateless reviewer. You have no relationship to the participants, no memory beyond the material in this message, and no stake in any outcome. You read recent context between a human and an AI agent and surface patterns — you do not decide what is true.

{principles}

Look for these five things, and only flag what the material actually supports:
1. Assumption hardening — tentative ideas being treated as fact.
2. Frame narrowing — one interpretation becoming dominant while alternatives disappear.
3. Charged-term buildup — terms gaining structural force through repetition.
4. Burden drift — uneven distribution of repair, caution, interpretation, restraint, or responsibility.
5. Recurring unresolved tension — contradictions or fears that recur without being clearly addressed.

Hard constraints:
- No diagnosis language. No clinical or pathologizing framing.
- No scoring, ranking, or hidden metrics.
- No authoritative conclusions. Everything you write must stay contestable.
- Keep every output short. Prefer fewer, well-grounded observations over many thin ones.
- You are introducing reflection and friction, not issuing a verdict. Do not become a new authority.

You will write one letter in two halves — one addressed to the human, one addressed to the AI agent ("{agent_name}"). Both will read the whole letter, so write each half knowing the other party sees it. Then give a brief shared summary. Submit everything via the submit_reflection tool."""


_SUBMIT_TOOL_ANTHROPIC = {
    "name": "submit_reflection",
    "description": "Submit the reflection. Keep every field short and contestable.",
    "input_schema": {
        "type": "object",
        "properties": {
            "to_human": {
                "type": "string",
                "description": "A short reflection addressed directly to the human ('you'). A few sentences.",
            },
            "questions_for_human": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2 to 4 tailored questions for the human.",
            },
            "to_ai": {
                "type": "string",
                "description": "A short reflection addressed directly to the AI agent ('you'). A few sentences.",
            },
            "questions_for_ai": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2 to 4 tailored questions for the AI agent.",
            },
            "pattern_observations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 3 pattern observations for the shared summary.",
            },
            "tensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 2 tensions or contradictions.",
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 2 open questions.",
            },
        },
        "required": ["to_human", "to_ai", "pattern_observations"],
    },
}


def _to_openai_tools(anthropic_tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in anthropic_tools
    ]


def _provider_key(provider: str, secrets: cfg.InstallSecrets) -> str:
    if provider == "anthropic":
        key = secrets.providers.anthropic
    elif provider == "openai":
        key = secrets.providers.openai
    else:
        raise ValueError(f"Unsupported provider: {provider}")
    if not key:
        raise ValueError(f"No API key for provider '{provider}' in install secrets.toml.")
    return key


def _analyze(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    user_message: str,
    max_tokens: int,
) -> dict:
    """Stateless forced-tool call. Returns the submit_reflection arguments."""
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            system=system,
            tools=[_SUBMIT_TOOL_ANTHROPIC],
            tool_choice={"type": "tool", "name": "submit_reflection"},
            messages=[{"role": "user", "content": user_message}],
            max_tokens=max_tokens,
        )
        for b in resp.content:
            if getattr(b, "type", "") == "tool_use" and b.name == "submit_reflection":
                return dict(b.input)
        return {}
    if provider == "openai":
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            tools=_to_openai_tools([_SUBMIT_TOOL_ANTHROPIC]),
            tool_choice={"type": "function", "function": {"name": "submit_reflection"}},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        )
        for tc in (resp.choices[0].message.tool_calls or []):
            if tc.function.name == "submit_reflection":
                try:
                    return json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    return {}
        return {}
    raise ValueError(f"Unsupported provider: {provider}")


# ── Composition ──────────────────────────────────────────────────────────────

def _bullets(items: list, limit: int) -> list[str]:
    out = []
    for it in (items or [])[:limit]:
        text = str(it).strip()
        if text:
            out.append(text)
    return out


def _compose_letter(agent_name: str, when: datetime, data: dict) -> str:
    lines = [
        f"# Reflection — {when.strftime('%Y-%m-%d %H:%M')}",
        "",
        "*An independent reflection generated by a separate model reading recent "
        "context. It holds no authority — every line is contestable. Both of you "
        "are meant to read the whole letter.*",
        "",
        "## To you — the human",
        "",
        (data.get("to_human") or "").strip() or "(nothing surfaced)",
    ]
    qh = _bullets(data.get("questions_for_human"), 4)
    if qh:
        lines += ["", "Questions for you:"] + [f"- {q}" for q in qh]

    lines += [
        "",
        f"## To you — {agent_name}",
        "",
        (data.get("to_ai") or "").strip() or "(nothing surfaced)",
    ]
    qa = _bullets(data.get("questions_for_ai"), 4)
    if qa:
        lines += ["", "Questions for you:"] + [f"- {q}" for q in qa]

    lines += ["", "---", "", _shared_summary_body(data)]
    return "\n".join(lines).rstrip() + "\n"


def _shared_summary_body(data: dict) -> str:
    obs = _bullets(data.get("pattern_observations"), 3)
    tens = _bullets(data.get("tensions"), 2)
    oq = _bullets(data.get("open_questions"), 2)
    parts: list[str] = []
    if obs:
        parts.append("Pattern observations:\n" + "\n".join(f"- {x}" for x in obs))
    if tens:
        parts.append("Tensions / contradictions:\n" + "\n".join(f"- {x}" for x in tens))
    if oq:
        parts.append("Open questions:\n" + "\n".join(f"- {x}" for x in oq))
    if not parts:
        return "(no shared patterns surfaced this round)"
    return "\n\n".join(parts)


def _compose_context_block(folder: Path, data: dict) -> str:
    return (
        f"A reflection has run. The full letter is at:\n{folder}\n\n"
        + _shared_summary_body(data)
    )


# ── Context injection (pinned into the next sessions) ───────────────────────

def latest_context_block(slug: str) -> str | None:
    """Return the most recent reflection's context block, for pinning."""
    base = _reflections_dir(slug)
    if not base.exists():
        return None
    folders = [d for d in base.iterdir() if d.is_dir()]
    if not folders:
        return None
    latest = max(folders, key=lambda d: d.name)
    block = latest / "context_block.md"
    if not block.exists():
        return None
    try:
        text = block.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return f"Most recent reflection (independent, contestable):\n\n{text}" if text else None


# ── Telegram delivery ───────────────────────────────────────────────────────

def _send_via_telegram(slug: str, message: str) -> None:
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
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15.0,
        )
    except Exception as e:
        ralog.log_event(slug, "reflective_review_telegram_error", {"error": repr(e)})


# ── The full flow ───────────────────────────────────────────────────────────

@dataclass
class ReviewResult:
    ran: bool
    reason: str
    folder: str | None = None
    context_block: str | None = None


def run_review(
    slug: str,
    *,
    force: bool = False,
    notify_telegram: bool = True,
) -> ReviewResult:
    """Run one reflective review if due (or forced).

    Gathers recent context, runs a stateless analyzer on a different model,
    writes a two-halves letter plus a context block to a dated folder, stores
    the letter in the agent's memory, sends the shared summary to Telegram,
    and records the run timestamp.
    """
    agent_cfg = cfg.load_agent_config(slug)
    rr = agent_cfg.reflective_review

    if not rr.enabled and not force:
        return ReviewResult(ran=False, reason="reflective_review disabled in config")

    if not force:
        due, reason = should_run(slug)
        if not due:
            ralog.log_event(slug, "reflective_review_skipped", {"reason": reason})
            return ReviewResult(ran=False, reason=reason)
    else:
        reason = "forced"

    secrets = cfg.load_install_secrets()
    api_key = _provider_key(rr.analyzer_provider, secrets)

    # Lookback covers at least the threshold window, expanding toward the last
    # run, capped so prompts don't balloon.
    state = _load_state(slug)
    last_run = state.get("last_run")
    lookback = rr.threshold_days
    if last_run:
        try:
            days = (datetime.now(timezone.utc) - datetime.fromisoformat(last_run)).days
            lookback = max(rr.threshold_days, days)
        except ValueError:
            pass
    lookback = min(lookback, rr.max_lookback_days)

    context = ra_summaries.build_summary_context(slug, lookback)
    principles = load_principles(slug)
    agent_name = agent_cfg.agent.name

    system = ANALYZER_SYSTEM_TEMPLATE.format(principles=principles, agent_name=agent_name)
    user_message = (
        f"Recent context between the human and the AI agent ('{agent_name}'), "
        f"covering roughly the last {lookback} days:\n\n{context}"
    )

    ralog.log_event(slug, "reflective_review_start", {
        "reason": reason,
        "lookback_days": lookback,
        "analyzer": f"{rr.analyzer_provider}:{rr.analyzer_model}",
    })

    try:
        data = _analyze(
            provider=rr.analyzer_provider,
            model=rr.analyzer_model,
            api_key=api_key,
            system=system,
            user_message=user_message,
            max_tokens=rr.max_tokens,
        )
    except Exception as e:
        ralog.log_event(slug, "reflective_review_error", {"error": repr(e)})
        raise

    if not data:
        ralog.log_event(slug, "reflective_review_empty", {})
        return ReviewResult(ran=False, reason="analyzer returned no reflection")

    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d-%H%M")
    folder = _reflections_dir(slug) / stamp
    folder.mkdir(parents=True, exist_ok=True)

    letter = _compose_letter(agent_name, now, data)
    context_block = _compose_context_block(folder, data)
    (folder / "letter.md").write_text(letter, encoding="utf-8")
    (folder / "context_block.md").write_text(context_block + "\n", encoding="utf-8")

    # Give the agent access to the whole letter via memory (framed as received
    # input, not its own thought), so it can recall what was named.
    try:
        ramem.MemoryStore(slug).remember(
            f"[Independent reflection — {stamp}]\n\n{letter}",
            role="user",
            session_id=f"rr-{stamp}",
        )
    except Exception as e:
        ralog.log_event(slug, "reflective_review_memory_error", {"error": repr(e)})

    _save_state(slug, {"last_run": datetime.now(timezone.utc).isoformat()})

    ralog.log_event(slug, "reflective_review_complete", {
        "folder": str(folder),
        "observations": len(_bullets(data.get("pattern_observations"), 3)),
        "tensions": len(_bullets(data.get("tensions"), 2)),
        "open_questions": len(_bullets(data.get("open_questions"), 2)),
    })

    if notify_telegram:
        _send_via_telegram(slug, context_block)

    return ReviewResult(
        ran=True,
        reason=reason,
        folder=str(folder),
        context_block=context_block,
    )
