# Streamlit GUI spec — Return Architecture

## Purpose

A local control panel for managing a Return Architecture installation. The
intended user is someone who has gotten through a one-line install and now
needs to: paste their API keys, create their agent, set up Telegram, choose
which schedules to enable, point an MCP filesystem server at a folder, and
edit the system prompt. After setup, they come back occasionally to adjust
schedules, view letters, browse items, or restart the service.

It is **not** a chat interface, not a logs aggregator, not a real-time
dashboard. The agent is reached via Telegram; this GUI manages the *thing
itself*, not conversation with it.

## How it runs

- Command: `return-architecture gui` starts the Streamlit app on a local
  port (default 8501, configurable via `gui.port` in install config) and
  opens the user's default browser.
- One Python file or a small set of files under `return_architecture/gui/`.
- The Streamlit process reads and writes the existing on-disk config files
  (`config.toml`, `secrets.toml`, `system_prompt.md`, schedules, etc.) via
  the same path-resolution module the rest of the package uses. No new
  storage layer.
- The GUI never holds state of its own across sessions. Every page render
  reads from disk. Every save writes to disk. No "draft" persistence.
- The daemon and the GUI are independent processes. The daemon does not
  watch config files for changes. Edits made in the GUI require a service
  restart to take effect — surfaced as a "Save & reload service" button
  that runs uninstall + install.

## Principles

- **Files on disk are the source of truth.** The GUI is a renderer + editor,
  not a database. A user could ignore the GUI and edit files by hand and the
  GUI would reflect their edits next time it loads.
- **Friction stays where it belongs.** API keys are a paste action, not a
  hidden prompt. The system prompt is a long-form editable text, not a
  hidden default. Important changes ("restart service", "delete agent")
  require explicit confirmation.
- **Two modes: setup and ongoing.** On first run with no agents and no
  keys, the GUI opens to a guided setup. After that, it opens to the
  agent's overview page by default.
- **No telemetry, no network calls** other than the user's own API key
  validations.

## Page structure

Streamlit multipage app. Sidebar lists the pages. The currently selected
agent (a dropdown at the top of the sidebar) scopes the per-agent pages.

```
┌─ Sidebar ─────────────────┐
│  Agent: [ myagent  ▾ ]    │
│  • Overview               │
│  • System prompt          │
│  • Tools                  │
│  • Schedules              │
│  • Telegram               │
│  • Artifact exchange      │
│  • Browse                 │
│    └─ Letters / Items /   │
│       Question responses  │
│  • Service                │
│  ─────────────             │
│  • Install settings       │
│  • About                  │
└────────────────────────────┘
```

If there are no agents on disk: sidebar collapses, full-page setup wizard
runs instead.

### 1. Overview (per agent)

- Agent name, slug, model, provider — read-only block.
- Service status: loaded / not loaded, PID if running.
- Last activity timestamp from logs.
- Counts: open items by kind, letters in outbox, pending inbox files,
  recent question sessions.
- Quick links to other pages.

### 2. System prompt (per agent)

- Large textarea pre-loaded with the agent's `system_prompt.md`.
- "Save" button writes back. "Save & reload service" button additionally
  reinstalls the service so the new prompt takes effect immediately.

### 3. Tools (per agent)

- **Built-in tools**: a checkbox list (`no_response`, `send_to_human_telegram`,
  `artifact_delete_reaction`, `artifact_share_more`, `tag_item`,
  `write_letter`). `no_response` is always on and not toggleable.
- **MCP servers**: list of configured servers with their command/args/env.
  Per-server: edit, delete, or add new. For the bundled `filesystem` server,
  a friendlier UI: pick a folder path with a directory picker, toggle
  read-only.
- Per-agent **artifact exchange** sub-config is on its own page (see #6),
  not here.

### 4. Schedules (per agent)

- List of all schedules with: name, kind, cron, enabled toggle, prompt.
- "Add schedule" button with kind dropdown (regular / daily_summary /
  weekly_summary / monthly_summary / question_session / question_pattern).
- For each: a cron-builder helper (day/hour/minute pickers that produce a
  cron string), or a raw cron text field for advanced users.
- "Delete schedule" with confirmation.

### 5. Telegram (per agent)

- Bot token field (write-only — shows "set" / "not set"; pasting overwrites).
- chat_id field, plus a "Discover chat_id" button that runs the existing
  `telegram-discover` helper and offers a click-to-set if any IDs found.
- Status: bot connected (via a test call), chat_id matches, etc.
- Help text reminding the user to register the bot with BotFather, etc.

### 6. Artifact exchange (per agent)

- Toggle: enabled.
- Mediator provider (anthropic / openai), mediator model.
- Agent and mediator max-tokens.
- A "Run a test exchange now" button that triggers `artifact-run` (only
  if `<agent>/artifacts/incoming/` has content) — confirmation required.

### 7. Browse (per agent — multi-tab page)

- **Letters** tab: list outbox files newest first; click to read full content.
  Delete with confirmation. Read-only — agent writes, human reads.
- **Inbox** tab: list inbox files; click to read; option to "trigger
  agent to read pending" (runs `/inbox` equivalent — needs daemon
  running).
- **Items** tab: filter by kind (note / important / question / commitment),
  by status (open / resolved). Resolve / archive controls.
- **Question responses** tab: list answered + skipped responses by
  session_id; show patterns at a glance.

### 8. Service (per agent)

- Install / uninstall buttons with confirmation.
- Status block (label, plist path, loaded, PID).
- Recent stdout and stderr tails (last 40 lines, with refresh button).

### 9. Install settings (install-wide)

- API keys: anthropic, openai (paste fields, write-only behaviour).
- Default agent (dropdown).
- GUI port.
- Log retention days.

### 10. About

- Version, install root path, links to docs, license.

## Onboarding flow (first run)

Triggered when `<install_root>/secrets.toml` doesn't exist OR no agents
are present.

1. **Welcome** — short paragraph explaining what Return Architecture is.
2. **API keys** — paste fields; at least one provider key required.
3. **Create agent** — name (display + slug), model, brief overview of what
   "agent" means here.
4. **System prompt** — show the default, allow editing, explain that this
   is the agent's identity and persists.
5. **Telegram setup** — guide through BotFather steps with screenshots
   *(out of scope for v1 of the GUI build; link to a docs page instead)*.
   Paste bot token. After paste, button to run discover and capture chat_id.
6. **Schedules** — show the available schedules with toggles. Recommended
   default off for everything; user opts in.
7. **Install service** — explain the launchd plist will be created and the
   daemon will start. Install button. Show status afterwards.
8. **Done** — go to the agent's Overview page.

Each step has Back / Next. State during the wizard is held in
`st.session_state`; written to disk only when the user proceeds past
a step where saving makes sense. Closing the browser mid-wizard loses
in-progress state but won't corrupt config.

## State and persistence model

- **Read on render**: every page-load reads the relevant config file(s) from
  disk. No long-lived caches. Streamlit's natural reactivity handles
  re-rendering when the user changes something.
- **Write on submit**: forms use `st.form` so writes are batched per
  submit, not per-keystroke. Saved values write through to the same
  files the daemon reads.
- **Stale-config warning**: when a page detects the file on disk has a
  newer mtime than the page's last-loaded version (e.g. another tab or
  the user edited the file in a text editor), show a "Reload" prompt
  before allowing further edits.
- **Service restart contract**: changes to config / system prompt / tools /
  schedules require the service to be reinstalled to take effect. The save
  button on each page is offered in two forms:
  - "Save" — writes to disk only. Daemon will pick up changes on its next
    restart.
  - "Save & reload service" — writes, then runs `service uninstall` +
    `service install`. Confirms first because reload drops in-memory chat
    history.

## Service integration

The GUI shells out to the existing CLI commands rather than re-implementing
service control:

- `return-architecture service install <slug>` and `uninstall <slug>`
- `return-architecture service status <slug>` (parsed for the UI block)
- `return-architecture service logs <slug>` (tail for the log viewer)
- `return-architecture telegram-discover <slug>` (for the chat_id helper)

This keeps the CLI as the canonical interface and avoids duplicating
launchd plist generation in two places.

## What's deliberately out of scope (v1)

- Real-time updates / websockets.
- Live conversation view or replaying chat history.
- Editing letters in the GUI (browse + delete only).
- Editing question responses.
- Multi-user / authentication. Localhost-bound; no auth.
- Mobile-responsive layout.
- Dark mode toggle (use Streamlit's built-in theme settings).
- Windows support for the service controls (separate from the GUI work).
- Cross-process notification (e.g. a banner that says "the daemon just
  emitted an error"). The user manually refreshes the service page.

## Resolved design decisions

The five questions originally open in v0.1 are now settled:

1. **Streamlit version & multipage style**: modern `st.Page` / `st.navigation`
   API (Streamlit ≥ 1.36). The Streamlit minimum version is pinned in
   `pyproject.toml`.
2. **Cron builder UI**: build it for v1. Radio for pattern (Daily / Weekly /
   Monthly / Custom), conditional fields per pattern, plus a "Custom (raw)"
   fallback for power users. Parses existing cron strings back into the
   picker where the pattern is simple; falls back to Custom otherwise.
3. **Telegram setup walkthrough**: docs link only for v1; the GUI shows a
   short paragraph + a link to a walkthrough page on the project website.
4. **Secrets handling**: never display back. UI shows "set ✓" or "not set",
   and the only interaction is a paste-to-overwrite field that's hidden
   behind a "Replace" affordance.
5. **Confirmation pattern for destructive actions**:
   - Typed confirmation (must type the agent slug / letter filename) for
     deleting an agent or a letter.
   - One-click action for service reload and uninstall, with a 3-second
     undo banner before the action commits.

## Status

- **Version:** 0.2
- **Date:** 2026-05-15
- **All decisions locked.** Ready to scaffold.
