# On-disk layout for an agent

The contract every other piece of Return Architecture depends on: where things live on the user's machine, and what each file and folder contains.

This document is the foundation. The runtime, GUI, scheduler, installer, and tool system all read and write through this layout. Keeping it stable matters more than keeping it pretty.

## Principles

- **Visible, not hidden.** Install root is `~/return-architecture/`, not `~/.return-architecture/`. Legibility over convention. The user should be able to open the folder, browse it, read their own files in plain text.
- **One folder per agent.** No shared identity state across agents. Adding an agent is creating a folder; removing one is deleting a folder.
- **Human-readable formats by default.** TOML for config, plain markdown for system prompts and reflection, sqlite for structured stores. Avoid binary blobs or proprietary serialisations unless there is no choice (Chroma is the one exception).
- **Provider-agnostic.** The agent doesn't know whether it's talking to Anthropic, OpenAI, or another provider until runtime. The model choice is configuration.
- **Per-install vs. per-agent split.** Anything tied to a person (LLM provider keys, install-wide preferences) lives at the install root. Anything tied to a relationship (Telegram bot, memory, schedules, system prompt) lives inside the agent folder.

## Top-level layout

```
~/return-architecture/
├── config.toml               # install-wide preferences
├── secrets.toml              # LLM provider API keys (chmod 600)
├── agents/
│   ├── <agent-slug>/         # one folder per agent
│   └── ...
├── logs/                     # install-wide logs (GUI, scheduler, installer)
└── .version                  # installed version, for migrations
```

### `config.toml` (install-wide)

```toml
[install]
created_at = "2026-05-13T10:00:00Z"
default_agent = "first-agent"   # which agent opens by default in the GUI

[gui]
port = 7878                     # localhost port for the GUI
open_browser_on_start = true

[ui]
show_cost_estimates = true      # token/$ counter in the GUI

[logs]
retention_days = 90             # raw conversation/tool/cost logs are deleted after this
```

### `secrets.toml` (install-wide)

LLM provider keys are per-human, not per-agent. One human, one set of provider credentials.

```toml
[providers]
anthropic = "sk-ant-..."
openai    = "sk-..."
# add others as supported
```

File permissions: `chmod 600` on creation. The installer and GUI enforce this.

## Per-agent layout

Each agent gets a self-contained folder. Deleting it deletes the entire agent — memory, schedules, writing, everything — with nothing left behind elsewhere.

```
~/return-architecture/agents/<agent-slug>/
├── config.toml               # agent identity, model, tools
├── secrets.toml              # per-agent tokens (Telegram, Discord, etc.)
├── system_prompt.md          # the agent's system prompt, in markdown
├── memory/                   # ChromaDB vector store (this agent only)
├── private/                  # AI-only reflection (read+write by agent, never sent to human)
├── outbox/                   # AI writes for human to read
├── inbox/                    # human writes for AI to read
├── artifacts/                # artifact exchange sessions (see below)
├── items.db                  # tagged items: notes, important, questions, commitments
├── schedules.db              # APScheduler jobstore (this agent's scheduled jobs)
├── sessions/                 # transcripts of structured sessions (question/commitment sessions)
├── logs/                     # this agent's conversation and tool-call logs
└── .meta.toml                # created_at, last_active, schema_version
```

### `<agent>/config.toml`

```toml
[agent]
name = "First Agent"              # display name shown in GUI
slug = "first-agent"              # folder name, also used in routing

[model]
provider = "anthropic"            # anthropic | openai | ...
name = "claude-opus-4-7"
max_tokens = 4096
temperature = 1.0

[behavior]
silence_allowed = true            # the no_response tool is always enabled regardless,
                                  # but this biases the system prompt toward it
max_self_scheduled_jobs_per_day = 5

[tools]
# MCP servers enabled for this agent, by id.
# Each entry corresponds to an MCP server installed via the GUI.
enabled = [
  "no_response",                  # always present, can't be disabled
  "send_to_human_telegram",
  "reflection_write",
  "tag_item",
  "url_fetch",
]

[tools.permissions]
# Fine-grained scopes per tool, where the tool supports them.
# Defaults shown; the GUI lets the user adjust.
reflection_write = { paths = ["private/"] }
filesystem_read  = { paths = ["inbox/", "outbox/", "private/"] }

[tools.artifact_exchange]
# The mediator should be a different model than the agent — ideally a different provider —
# so the witness is genuinely separate, not just a stateless instance of the same model.
mediator_provider    = "openai"
mediator_model       = "gpt-5.5"
agent_max_tokens     = 600
mediator_max_tokens  = 600
```

### `<agent>/secrets.toml`

Per-agent because the same human might run two agents with two different Telegram bots, or one agent that uses Telegram and another that uses Discord.

```toml
[telegram]
bot_token = "..."
chat_id   = "..."           # the human's chat id

[discord]
# only present if Discord is enabled for this agent
bot_token = "..."
```

`chmod 600` on creation, same as install-wide secrets.

### `<agent>/system_prompt.md`

Plain markdown. The user edits this in the GUI but can also open it in any editor. The agent's identity is *literally* this file plus the tools and memory — it should be readable and meaningful on its own.

### `<agent>/memory/`

ChromaDB persistent client directory. One collection per agent (no cross-agent leakage). The schema is determined by the embedding model and Chroma's own format; we don't try to make this human-readable.

### `<agent>/private/`, `<agent>/outbox/`, `<agent>/inbox/`

Three distinct folders for three distinct kinds of writing. They are *deliberately* separated — private reflection must never mix with human-facing writing.

- `private/` — **agent-only.** The agent reads and writes here. Never surfaced to the human, never sent over Telegram, never included in any digest or summary that goes to the human. This is the agent's reflection space. The human does not write here.
- `outbox/` — **agent → human, outside chat.** The agent writes longer-form material here when it wants to leave something for the human to read at their own pace (a letter, a worked-through thought, a summary). The human reads but does not write.
- `inbox/` — **human → agent, outside chat.** The human writes longer-form material here when they want the agent to take it in (an article they wrote, a long reflection, a poem). The agent reads but does not write.

`inbox/` is **pull-based, not push-based**. New files do not ping the agent. The agent reads `inbox/` when:
- The human sends a Telegram command (e.g., `/inbox`) asking the agent to look.
- A scheduled ping fires and the agent chooses, among other possible activities, to read pending inbox items.

This is intentional: pulling rather than pushing keeps attention deliberate and avoids interrupting the agent with every saved file.

File naming convention: `YYYY-MM-DD-HHmm-<slug>.md`.

### `<agent>/items.db` (sqlite)

The tagged-item store. One table, polymorphic by `kind`.

```sql
CREATE TABLE items (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,        -- 'note' | 'important' | 'question' | 'commitment'
  body        TEXT NOT NULL,
  source      TEXT NOT NULL,        -- 'human' | 'agent'
  source_ref  TEXT,                 -- telegram message id, session id, etc.
  status      TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'resolved' | 'archived'
  created_at  TEXT NOT NULL,
  resolved_at TEXT,
  metadata    TEXT                  -- JSON blob for kind-specific fields
);

CREATE INDEX idx_items_kind_status ON items(kind, status);
```

Tags from Telegram (`#note`, `#question`, `#important`, `#commitment`) write here. The daily/weekly/monthly digests read from here.

### `<agent>/schedules.db` (sqlite)

APScheduler's SQLAlchemyJobStore. Survives restarts. Stores:
- Recurring jobs (daily morning ping, weekly digest, monthly summary)
- One-off jobs the agent set for itself via `schedule_self`
- One-off jobs the human added via the GUI

The GUI reads from this to display "what's scheduled" and lets the user pause, edit, or delete jobs.

### `<agent>/artifacts/`

The artifact exchange is a deliberate, human-initiated ritual to interrupt sycophantic dynamics. The human offers a raw artifact (text, scene, optionally an image); the agent reacts privately; a stateless mediator (a different model, ideally a different provider) translates the reaction's *texture* — not its wording — into a signal for the human; the agent then decides what, if anything, the human sees **beyond** that signal.

**The mediator's signal is always delivered to the human.** This is the floor of the exchange, not an option. After offering an artifact, the human is never left with nothing in return — they receive at minimum the mediator's atmospheric reading. The agent's post-mediation decision governs only what is layered on top:

- `signal_only` — human receives the mediator's signal, nothing more.
- `share_reaction` — signal **plus** the agent's raw reaction.
- `share_all` — signal **plus** the raw reaction **plus** the agent's post-mediation reflection.

The agent decides depth, not delivery. There is no path through this flow where the human's offering disappears into silence.

This is the only flow in Return Architecture that uses a second, memory-less model alongside the primary agent. It is also the only flow where the agent's first response is *not* delivered to the human by default — and where the agent is given an explicit affordance to delete its own reaction afterwards.

```
<agent>/artifacts/
├── <YYYY-MM-DD-HHmm-slug>/        # one folder per exchange
│   ├── scene.md                   # the human's scene text
│   ├── image.<ext>                # optional image artifact
│   ├── .raw_reaction.md           # hidden; the agent's private reaction. Agent may delete.
│   ├── mediator.md                # mediator's reflection (for agent) + signal (for human)
│   ├── agent_response.md          # agent's post-mediation reflection + sharing decision
│   ├── for_human.md               # what the human actually sees, per the decision
│   └── meta.toml                  # timestamps, models used, decision, status
├── notes/                         # short notes to the agent about completed exchanges,
│                                  #   surfaced into context on the next session
└── shared/                        # later, optional sharing — agent writes here when it
                                   #   wants to surface something to the human after the fact
```

Per-exchange `meta.toml`:

```toml
created_at           = "2026-05-13T10:00:00Z"
agent_provider       = "anthropic"
agent_model          = "claude-opus-4-7"
mediator_provider    = "openai"             # ideally different from the agent's provider
mediator_model       = "gpt-5.5"
decision             = "signal_only"        # signal_only | share_reaction | share_all
raw_reaction_kept    = true                 # false once the agent has chosen to delete it
```

**Hidden file convention.** `.raw_reaction.md` is dot-prefixed so it does not appear in casual directory listings (macOS Finder, `ls`). This is a soft protection — anyone who looks for it can find it — but it matches the spirit: the human is not meant to read it unless the agent has chosen to share. On Windows the dotfile convention is not enforced; the GUI should additionally hide files matching `.raw_reaction.md` from any artifact browser it exposes.

**Agent-side affordances** (delivered as MCP tools, not by reaching into the filesystem):
- `artifact_delete_reaction(exchange_id)` — deletes `.raw_reaction.md` for a given exchange. Updates `meta.toml`. Cannot be undone.
- `artifact_share_more(exchange_id, content, label)` — writes to `artifacts/shared/` and notifies the human.

**Human-side affordances:**
- Telegram command `/artifact` (with attached image and/or scene text) — starts a new exchange.
- GUI panel for "Start an artifact exchange" with image upload and scene textarea — same flow, no terminal needed.
- After completion, the human receives a Telegram message: where to find the signal, that the session is done, an invitation to talk about it whenever they want.

**Mediator configuration** lives in the agent's `config.toml` under `[tools.artifact_exchange]`. The mediator's provider key uses the install-wide `secrets.toml` like any other LLM call — no separate credential.

### `<agent>/sessions/`

Each structured session (question session, commitment session) writes a transcript here as a markdown file plus a `.meta.json` sidecar with the captured items. The session primitive references these so that future context loads can include them.

### `<agent>/logs/`

Append-only NDJSON files, one per day:
- `conversations-YYYY-MM-DD.ndjson` — every message, tool call, tool result
- `tools-YYYY-MM-DD.ndjson` — tool invocations with arguments and outputs
- `costs-YYYY-MM-DD.ndjson` — per-call token counts and estimated cost

Rotated daily. **Retained for 90 days by default**, then deleted. The user can adjust the retention window in install `config.toml` (`[logs] retention_days`).

Logs are operational artefacts, not memory. They are *not* a substitute for backup. The GUI surfaces this clearly: users who want long-term records of a relationship should rely on the agent's memory, the tagged-item store, the writing folders, and scheduled summaries — not on raw conversation logs. The GUI links to a short guide on backup strategies (export tagged items to markdown, save summaries to outbox, keep a copy of the agent folder).

### `<agent>/.meta.toml`

```toml
created_at      = "2026-05-13T10:00:00Z"
last_active_at  = "2026-05-13T15:42:00Z"
schema_version  = 1
```

`schema_version` lets future versions migrate this agent's data forward without guessing.

## Naming & slug conventions

- Slug: lowercase, hyphens, ASCII. `[a-z0-9-]+`. Generated from the display name on creation, editable in the GUI but with care (renaming a slug means renaming the folder).
- Display name: any unicode, used in GUI only.
- File timestamps in ISO 8601 with timezone. UTC for stored data; local time only for display.

## What this layout does *not* include

Things we discussed that don't need new top-level structure:

- **MCP server installations** live in the standard pipx/uv tool location, not in this tree. The agent config only references them by id; the actual code is installed by `pipx`.
- **Telegram polling/webhook state** lives in the running process or in `schedules.db`, not as a separate file.
- **Browser session data, OAuth flows** — none yet; if added later they'd go in a per-agent `auth/` folder.
- **The "artifact tool with mediator"** — pending explanation. Likely a new folder (`artifacts/`) and a table in `items.db` or a new sqlite file. Not blocking this spec.

## Resolved design decisions

These were open in v0.1 and have now been settled:

1. **One ChromaDB collection per agent.** No shared memory across agents. Memory isolation enforces identity isolation.
2. **Log retention: 90 days, then deleted.** Logs are operational, not archival. The user is encouraged to maintain their own backups via tagged items, outbox summaries, and copies of the agent folder. The GUI links to a short guide on this.
3. **`inbox/` is pull-based, not push-based.** New files do not interrupt the agent. The agent reads `inbox/` only when a Telegram command requests it, or when a scheduled ping fires and the agent chooses reading among possible activities.
4. **`private/` is agent-only.** The human does not write there. Their writing-to-agent lives in `inbox/`, which is separate from the agent's reflection space.
5. **Schema migrations auto-run with a backup taken first.** On version bumps, the agent folder is copied to `~/return-architecture/agents/<slug>.backup-YYYY-MM-DD/` before migration applies.

## Migration to v0.2

On schema version bumps, the runtime:
1. Takes a full copy of the agent folder, suffixed with `.backup-YYYY-MM-DD`.
2. Applies the migration in place.
3. Updates `.meta.toml` with the new `schema_version`.
4. Reports both the backup path and the migration outcome in the GUI.

Old backups are not auto-deleted — the user controls when those go.

## Implied Telegram commands

This layout assumes a small set of Telegram slash-commands the agent recognises. Documenting them here because they shape what `inbox/` and other stores need to support:

- `/inbox` — agent reads pending `inbox/` files and responds.
- `/notes`, `/questions`, `/important`, `/commitments` — agent surfaces open items of that kind.
- `/silence on|off` — temporarily mute scheduled agent-initiated messages.
- `/artifact` — start a new artifact exchange. Image attachment and/or scene text in the same message.

Full command spec belongs in the Telegram tool's own doc, not here. Listed for context.

## Status

- **Version:** 0.3
- **Date:** 2026-05-13
- **Decisions locked:** install root path, per-agent isolation, TOML config format, sqlite for items and schedules, markdown for prompt and writing folders, 90-day log retention, pull-based inbox, agent-only private, auto-migrate with backup, artifact-exchange storage with hidden raw reaction and stateless mediator (different provider from the agent).
- **Decisions pending:** none blocking. The first runtime can be built against this layout.
