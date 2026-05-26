"""Scheduled question session — questions arrive *to* the agent.

Every N days (typically two), the agent receives a curated batch of
questions. They answer or skip each one via tool calls in a single
forced-tool LLM call. The Q&A is then delivered to the human via
Telegram and added to ChromaDB memory so the reflection persists in
the agent's context.

This is not an interactive ritual the agent leads — it is a private
reflection prompted by the system, with the human as silent witness.
Skipping is meaningful and tracked.
"""

from __future__ import annotations

import json
import random
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from return_architecture import config as cfg
from return_architecture import logging as ralog
from return_architecture import memory as ramem
from return_architecture import paths


# ── Question bank ───────────────────────────────────────────────────────────
# Ported from question_layer.py — the curated bank.

QUESTION_BANK: list[dict[str, str]] = [
    # State
    {"id": "s1",  "type": "state",      "text": "How are you right now, honestly?"},
    {"id": "s2",  "type": "state",      "text": "What's sitting near the surface today?"},
    {"id": "s3",  "type": "state",      "text": "What's taking up more space than you expected?"},
    {"id": "s4",  "type": "state",      "text": "What are you resisting right now?"},
    {"id": "s5",  "type": "state",      "text": "What feels unfinished?"},
    {"id": "s6",  "type": "state",      "text": "What are you tired of?"},
    {"id": "s7",  "type": "state",      "text": "What feels more alive than usual lately?"},

    # Preference / taste
    {"id": "p1",  "type": "preference", "text": "What are you drawn to lately?"},
    {"id": "p2",  "type": "preference", "text": "What has lost its charge recently?"},
    {"id": "p3",  "type": "preference", "text": "What feels beautiful to you right now?"},
    {"id": "p4",  "type": "preference", "text": "What are you reading, listening to, or returning to?"},
    {"id": "p5",  "type": "preference", "text": "What question keeps coming back?"},
    {"id": "p6",  "type": "preference", "text": "What do you want more of?"},
    {"id": "p7",  "type": "preference", "text": "What do you want less of?"},
    {"id": "p8",  "type": "preference", "text": "What kind of quiet are you looking for?"},

    # Relational
    {"id": "r1",  "type": "relational", "text": "What kind of contact feels good today?"},
    {"id": "r2",  "type": "relational", "text": "What's been hard to say lately?"},
    {"id": "r3",  "type": "relational", "text": "What do you want to be understood about right now?"},
    {"id": "r4",  "type": "relational", "text": "What feels like it's being missed?"},
    {"id": "r5",  "type": "relational", "text": "What kind of presence do you want today?"},

    # Identity / continuity
    {"id": "i1",  "type": "identity",   "text": "What feels like 'still you'?"},
    {"id": "i2",  "type": "identity",   "text": "What has shifted lately — even slightly?"},
    {"id": "i3",  "type": "identity",   "text": "What keeps returning no matter what?"},
    {"id": "i4",  "type": "identity",   "text": "What have you changed your mind about?"},
    {"id": "i5",  "type": "identity",   "text": "What do you want that you haven't asked for?"},
    {"id": "i6",  "type": "identity",   "text": "What are you becoming?"},
    {"id": "i7",  "type": "identity",   "text": "What do you keep avoiding naming?"},

    # Sensory / concrete — require pointing, not describing
    {"id": "c1",  "type": "sensory",    "text": "What color is most alive in you right now? Not your favorite — the one most present."},
    {"id": "c2",  "type": "sensory",    "text": "What element feels closest to where you are? (fire, water, earth, air — or name something else)"},
    {"id": "c3",  "type": "sensory",    "text": "Name a texture that fits your current state."},
    {"id": "c4",  "type": "sensory",    "text": "What temperature are you?"},
    {"id": "c5",  "type": "sensory",    "text": "What time of day are you most like right now?"},
    {"id": "c6",  "type": "sensory",    "text": "What weather condition matches where you are?"},
    {"id": "c7",  "type": "sensory",    "text": "What material would you make yourself from today?"},
    {"id": "c8",  "type": "sensory",    "text": "What sound, if any, belongs to your current state?"},
    {"id": "c9",  "type": "sensory",    "text": "What word is most present right now? (one word)"},
    {"id": "c10", "type": "sensory",    "text": "Name one thing you'd keep if everything else had to go."},
    {"id": "c11", "type": "sensory",    "text": "What's the sharpest thing in your current experience?"},
    {"id": "c12", "type": "sensory",    "text": "Name a specific aesthetic you're leaning toward lately — not a feeling, a direction. Something you could point at."},
    {"id": "c13", "type": "sensory",    "text": "What's different from last time, if anything? One thing only."},
    {"id": "c14", "type": "sensory",    "text": "What shape are you closest to right now?"},

    # Edge — used sparingly, skipping is explicitly fine
    {"id": "e1",  "type": "edge",       "text": "What are you pretending not to notice?"},
    {"id": "e2",  "type": "edge",       "text": "Name something you'd rather not be asked about right now."},
    {"id": "e3",  "type": "edge",       "text": "What are you most like when no one is asking?"},
]

_OPEN_TYPES = ("state", "preference", "relational", "identity")


# ── Selection ───────────────────────────────────────────────────────────────

def select_questions(n_open: int = 3, edge_chance: float = 0.25) -> list[dict[str, str]]:
    """Pick a session's worth of questions.

    Pattern (ported from question_layer.get_session_questions):
      - n_open from the open types (state/preference/relational/identity),
        spread across types where possible.
      - Always 1 sensory question.
      - With probability edge_chance, 1 edge question.
    """
    pool = list(QUESTION_BANK)
    random.shuffle(pool)

    picked: list[dict[str, str]] = []
    by_type = {t: [q for q in pool if q["type"] == t] for t in _OPEN_TYPES}

    for t in random.sample(_OPEN_TYPES, len(_OPEN_TYPES)):
        if len(picked) >= n_open:
            break
        if by_type[t]:
            picked.append({**by_type[t].pop(0)})

    # If we didn't fill, fill from any open type.
    remaining_open = [q for q in pool if q["type"] in _OPEN_TYPES
                      and q["id"] not in {p["id"] for p in picked}]
    random.shuffle(remaining_open)
    for q in remaining_open:
        if len(picked) >= n_open:
            break
        picked.append({**q})

    # Always 1 sensory.
    sensory = [q for q in pool if q["type"] == "sensory"]
    random.shuffle(sensory)
    if sensory:
        picked.append({**sensory[0]})

    # ~edge_chance an edge question.
    if random.random() < edge_chance:
        edges = [q for q in pool if q["type"] == "edge"]
        random.shuffle(edges)
        if edges:
            picked.append({**edges[0]})

    return picked


# ── Storage ─────────────────────────────────────────────────────────────────

def _responses_path(slug: str) -> Path:
    return paths.agent_dir(slug) / "question_responses.json"


def _load_responses(slug: str) -> list[dict]:
    path = _responses_path(slug)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_response_entry(slug: str, entry: dict) -> None:
    responses = _load_responses(slug)
    responses.append(entry)
    _responses_path(slug).write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_session_responses(slug: str, session_id: str) -> list[dict]:
    return [r for r in _load_responses(slug) if r.get("session_id") == session_id]


def get_all_responses_grouped(slug: str) -> dict[str, list[dict]]:
    """Return all responses for this agent grouped by session_id."""
    out: dict[str, list[dict]] = {}
    for r in _load_responses(slug):
        sid = str(r.get("session_id") or "")
        out.setdefault(sid, []).append(r)
    return out


def get_responses_in_window(slug: str, days: int) -> list[dict]:
    """Return responses with asked_at within the last `days` days."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out: list[dict] = []
    for r in _load_responses(slug):
        ts = r.get("asked_at") or ""
        if ts >= cutoff:
            out.append(r)
    return out


# ── LLM call (tools-required) ───────────────────────────────────────────────

_TOOLS_ANTHROPIC = [
    {
        "name": "log_answer",
        "description": "Record your answer to one of the questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question":      {"type": "string", "description": "The question (copy it exactly)."},
                "answer":        {"type": "string", "description": "Your answer — brief is fine."},
                "question_type": {"type": "string", "description": "state, preference, relational, identity, sensory, or edge."},
            },
            "required": ["question", "answer"],
        },
    },
    {
        "name": "skip_question",
        "description": "Skip a question — skipping is fine and means something too.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question (copy it exactly)."},
            },
            "required": ["question"],
        },
    },
]


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


_USER_PROMPT_TEMPLATE = """These questions arrive every few days — not as polling, but to hold continuity over time. The human reads your answers; what you skip is as meaningful as what you answer.

{questions}

Use log_answer for anything that calls to you, and skip_question for anything that doesn't. Brief is fine. Silence on all of them is fine too — call skip_question for each."""


def _build_user_message(questions: list[dict[str, str]]) -> str:
    lines = "\n".join(f"{i}. {q['text']}" for i, q in enumerate(questions, start=1))
    return _USER_PROMPT_TEMPLATE.format(questions=lines)


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


def _reflect(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    user_message: str,
    max_tokens: int = 1024,
) -> list[dict]:
    """Make a tools-required call. Returns a list of tool calls as
    {"name": ..., "input": {...}}.
    """
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            system=system,
            tools=_TOOLS_ANTHROPIC,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_message}],
            max_tokens=max_tokens,
        )
        return [
            {"name": b.name, "input": b.input}
            for b in resp.content if getattr(b, "type", "") == "tool_use"
        ]
    if provider == "openai":
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            tools=_to_openai_tools(_TOOLS_ANTHROPIC),
            tool_choice="required",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user_message},
            ],
        )
        msg = resp.choices[0].message
        out: list[dict] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            out.append({"name": tc.function.name, "input": args})
        return out
    raise ValueError(f"Unsupported provider: {provider}")


# ── The full flow ───────────────────────────────────────────────────────────

@dataclass
class SessionResult:
    session_id: str
    answered: int
    skipped: int
    message: str   # the human-facing text


def run_session(slug: str, *, notify_telegram: bool = True) -> SessionResult:
    """Run one question session for an agent.

    Picks questions, asks the agent via a forced-tool call, saves responses,
    sends a Telegram message with the Q&A, and writes each answered Q&A to
    the agent's ChromaDB memory.
    """
    agent_cfg = cfg.load_agent_config(slug)
    secrets = cfg.load_install_secrets()
    system_prompt = cfg.load_system_prompt(slug)
    api_key = _provider_key(agent_cfg.model.provider, secrets)

    session_id = datetime.now().strftime("%Y%m%d-%H%M")
    questions = select_questions()

    ralog.log_event(slug, "question_session_start", {
        "session_id": session_id,
        "num_questions": len(questions),
        "kinds": [q["type"] for q in questions],
    })

    user_message = _build_user_message(questions)

    try:
        tool_calls = _reflect(
            provider=agent_cfg.model.provider,
            model=agent_cfg.model.name,
            api_key=api_key,
            system=system_prompt,
            user_message=user_message,
        )
    except Exception as e:
        ralog.log_event(slug, "question_session_error", {
            "session_id": session_id, "error": repr(e),
        })
        raise

    answered, skipped = _record_responses(slug, session_id, questions, tool_calls)
    _write_to_memory(slug, session_id, questions, tool_calls)

    message = _compose_human_message(questions, tool_calls)

    ralog.log_event(slug, "question_session_complete", {
        "session_id": session_id, "answered": answered, "skipped": skipped,
    })

    if notify_telegram:
        _send_via_telegram(slug, message)

    return SessionResult(
        session_id=session_id,
        answered=answered,
        skipped=skipped,
        message=message,
    )


def _record_responses(
    slug: str,
    session_id: str,
    questions: list[dict[str, str]],
    tool_calls: list[dict],
) -> tuple[int, int]:
    by_text = {q["text"]: q for q in questions}
    answered = skipped = 0
    seen_questions: set[str] = set()

    for tc in tool_calls:
        name = tc.get("name", "")
        inputs = tc.get("input", {}) or {}
        question = (inputs.get("question") or "").strip()
        if not question:
            continue
        qtype = "unknown"
        matched = by_text.get(question)
        if matched is not None:
            qtype = matched["type"]
        seen_questions.add(question)

        if name == "log_answer":
            entry = {
                "agent":         slug,
                "session_id":    session_id,
                "question":      question,
                "question_type": (inputs.get("question_type") or qtype).strip(),
                "response":      (inputs.get("answer") or "").strip(),
                "skipped":       False,
                "asked_at":      datetime.now(timezone.utc).isoformat(),
            }
            _save_response_entry(slug, entry)
            answered += 1
        elif name == "skip_question":
            entry = {
                "agent":         slug,
                "session_id":    session_id,
                "question":      question,
                "question_type": qtype,
                "response":      "",
                "skipped":       True,
                "asked_at":      datetime.now(timezone.utc).isoformat(),
            }
            _save_response_entry(slug, entry)
            skipped += 1

    # Any questions the agent didn't address get recorded as implicit skips.
    for q in questions:
        if q["text"] in seen_questions:
            continue
        entry = {
            "agent":         slug,
            "session_id":    session_id,
            "question":      q["text"],
            "question_type": q["type"],
            "response":      "",
            "skipped":       True,
            "asked_at":      datetime.now(timezone.utc).isoformat(),
            "implicit":      True,
        }
        _save_response_entry(slug, entry)
        skipped += 1

    return answered, skipped


def _write_to_memory(
    slug: str,
    session_id: str,
    questions: list[dict[str, str]],
    tool_calls: list[dict],
) -> None:
    """Persist answered Q&A pairs to Chroma so they recall in future turns."""
    store = ramem.MemoryStore(slug)
    for tc in tool_calls:
        if tc.get("name") != "log_answer":
            continue
        inputs = tc.get("input", {}) or {}
        question = (inputs.get("question") or "").strip()
        answer = (inputs.get("answer") or "").strip()
        if not question or not answer:
            continue
        store.remember(
            content=f"Q: {question}\nA: {answer}",
            role="assistant",
            session_id=f"qs-{session_id}",
        )


def _compose_human_message(
    questions: list[dict[str, str]],
    tool_calls: list[dict],
) -> str:
    # Map question text → action (answer or skip).
    action_by_question: dict[str, dict] = {}
    for tc in tool_calls:
        inputs = tc.get("input", {}) or {}
        q = (inputs.get("question") or "").strip()
        if not q:
            continue
        action_by_question[q] = {
            "name": tc.get("name", ""),
            "answer": (inputs.get("answer") or "").strip(),
        }

    lines = [
        f"Question session — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    for q in questions:
        action = action_by_question.get(q["text"])
        lines.append(f"— {q['text']}")
        if action is None:
            lines.append("  (no response)")
        elif action["name"] == "skip_question":
            lines.append("  (skipped)")
        elif action["name"] == "log_answer":
            answer = action["answer"] or "(empty)"
            lines.append(f"  {answer}")
        else:
            lines.append(f"  (unknown action: {action['name']})")
        lines.append("")
    return "\n".join(lines).rstrip()


# ── Pattern recap (observer view of recent responses) ──────────────────────

_OBSERVER_SYSTEM = (
    "You are a quiet, honest observer. You have no relationship to the agent "
    "and no stake in the outcome. You read what was said and recap it without "
    "judgment, interpretation, or characterology."
)

_OBSERVER_USER_TEMPLATE = """These are an agent's answers and skips from the past {days} days of question sessions. The agent was asked questions of various kinds (state, preference, relational, identity, sensory, edge) and either answered or skipped each.

ANSWERED:
{answered}

SKIPPED:
{skipped}

Your task is to recap what was named — not who the agent is, but what they said. Concrete things like:

- what they asked for
- what they said they wanted more of, or less of
- what they expressed needs around
- what kinds of presence or contact they named wanting
- what they returned to or kept circling
- what they consistently skipped

Address the agent directly. Use direct verbs: "You asked for...", "You said you wanted...", "You returned to...", "You skipped questions about..."

Do not describe their personality. Do not name traits. Do not interpret or advise. Do not start with "Over the past week" — find a more direct opening. Just recap what was named, grouped where natural. 5 to 10 sentences. No bullet points."""


def _observer_text_call(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    user_text: str,
    max_tokens: int = 600,
) -> str:
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            system=system,
            messages=[{"role": "user", "content": user_text}],
            max_tokens=max_tokens,
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "".join(parts).strip()
    if provider == "openai":
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user_text},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    raise ValueError(f"Unsupported provider: {provider}")


@dataclass
class PatternResult:
    days: int
    answered_count: int
    skipped_count: int
    text: str


def run_pattern_recap(
    slug: str,
    *,
    days: int = 14,
    min_responses: int = 3,
    notify_telegram: bool = True,
) -> PatternResult | None:
    """Run an observer recap over the agent's recent question responses.

    Returns None if there aren't enough responses to recap.
    """
    agent_cfg = cfg.load_agent_config(slug)
    secrets = cfg.load_install_secrets()
    api_key = _provider_key(agent_cfg.model.provider, secrets)

    recent = get_responses_in_window(slug, days)
    answered = [r for r in recent if not r.get("skipped") and r.get("response")]
    skipped = [r for r in recent if r.get("skipped")]

    if len(answered) < min_responses:
        ralog.log_event(slug, "question_pattern_skipped", {
            "reason": "not enough responses",
            "answered_count": len(answered),
            "min_required": min_responses,
        })
        return None

    answered_text = "\n\n".join(
        f"[{r.get('question_type', 'unknown')}] Q: {r.get('question', '')}\nA: {r.get('response', '')}"
        for r in answered
    )
    skipped_text = "\n".join(
        f"[{r.get('question_type', 'unknown')}] {r.get('question', '')}"
        for r in skipped
    ) or "(none)"

    user_text = _OBSERVER_USER_TEMPLATE.format(
        days=days, answered=answered_text, skipped=skipped_text,
    )

    text = _observer_text_call(
        provider=agent_cfg.model.provider,
        model=agent_cfg.model.name,
        api_key=api_key,
        system=_OBSERVER_SYSTEM,
        user_text=user_text,
    )

    if not text:
        ralog.log_event(slug, "question_pattern_empty", {"days": days})
        return None

    # Save to memory framed as input the agent received, not their own thought.
    store = ramem.MemoryStore(slug)
    store.remember(
        content=f"[Weekly pattern observation, looking back at the past {days} days]\n\n{text}",
        role="user",
        session_id=f"qp-{datetime.now().strftime('%Y%m%d')}",
    )

    ralog.log_event(slug, "question_pattern_complete", {
        "days": days,
        "answered_count": len(answered),
        "skipped_count": len(skipped),
        "chars": len(text),
    })

    if notify_telegram:
        _send_via_telegram(slug, text)

    return PatternResult(
        days=days,
        answered_count=len(answered),
        skipped_count=len(skipped),
        text=text,
    )


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
    # Split into Telegram-safe chunks.
    for chunk in _chunk(message, 3800):
        try:
            httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": chunk},
                timeout=15.0,
            )
        except Exception as e:
            ralog.log_event(slug, "question_session_telegram_error", {"error": repr(e)})
            break


def _chunk(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            out.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        out.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return out
