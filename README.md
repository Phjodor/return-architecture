# Return Architecture

Local agent runtime for relational, long-term AI. Run your own agent on your own machine, with your own API keys, with continuity and intentional friction by design.

> **Status**: early beta. macOS-first. Linux works for the agent itself; full Linux support (background service) is on the roadmap. Windows isn't supported yet.

## What this is

Return Architecture is an alternative to short-session, tool-only, extractive AI patterns. Instead of opening a chat, asking, and closing it, the agent runs in the background, accumulates memory across sessions, follows a schedule of its own, reaches you via Telegram, and can choose to stay silent. Everything runs locally — your API keys, your conversations, your memory, your decisions about what the agent can do — none of it leaves your machine except for the LLM calls you authorise.

The system is designed around a few values:

- **Friction is a feature.** Smoothing every gesture away erodes intentionality. A one-command install is the right amount of friction; a hosted SaaS is too little.
- **Local-first, no telemetry, no accounts.** Nothing phones home. The website does not see your secrets.
- **Anti-extractive.** Silence is a first-class action. The agent choosing not to respond is a feature, not a failure.
- **Continuity over engagement.** Memory, ritual, scheduled returns, journaling — the architecture helps a relationship accumulate weight over time.

## Who this is for

People already comfortable using AI APIs who want a relationship with an AI that:
- has continuity across sessions
- runs on their machine with their keys
- can reach them on its own rhythm (not just when prompted)
- doesn't optimise for engagement
- they can adjust, extend, and own

Some terminal comfort is required — you'll run commands and edit a couple of configuration files. A local web GUI handles most ongoing settings.

## What you get

- **Coherent agent identity** — name, system prompt, model, all yours to edit
- **Cross-session memory** — local vector store (ChromaDB), no third-party storage
- **Two-way Telegram channel** — send messages, get replies, with image support
- **Scheduled pings** — agent-initiated moments on the cadence you choose
- **Daily / weekly / monthly summaries** — agent looks back over its own activity
- **Question sessions** — scheduled rituals where the agent reflects in private and shows the answers to you
- **Pattern recaps** — periodic outside-observer reads on the agent's recent answers
- **Artifact exchange** — a deliberate ritual for offering something charged, with a stateless mediator to interrupt sycophantic dynamics
- **Tagged items** — `#note`, `#question`, `#important`, `#commitment` from Telegram or by the agent itself
- **Letters** — longer-form writing between agent and human, outside the chat
- **Extensible via MCP** — bundled URL fetcher and filesystem servers; add any [MCP server](https://modelcontextprotocol.io/) you want
- **Background service** — runs without keeping a terminal open (macOS launchd; Linux support coming)
- **Local web GUI** — Streamlit-based control panel for everything: keys, prompt, schedules, tools, Telegram, browsing letters/items/responses, service controls

## Install (macOS)

Requires Python 3.11–3.13 and an Anthropic or OpenAI API key.

```bash
# Install uv (handles Python + virtualenv) if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Return Architecture from this repo via pipx
pipx install git+https://github.com/Theapolar/return-architecture
```

If you don't have `pipx`, install it with `brew install pipx` (Homebrew) or `python3 -m pip install --user pipx`, then `pipx ensurepath`.

## First run

Open the GUI and walk through the setup wizard:

```bash
return-architecture gui
```

The wizard takes about 5 minutes. You'll paste an API key, create an agent, optionally set up Telegram, choose scheduled rhythms, and install the background service. After that the agent runs in the background and you can reach it through Telegram.

If you'd rather skip the GUI:

```bash
return-architecture init myagent     # create an agent
# then add your API key to ~/return-architecture/secrets.toml
return-architecture chat myagent     # chat from the terminal
```

## Documentation

- [`docs/agent-layout.md`](docs/agent-layout.md) — the on-disk layout for an installation, agent by agent
- [`docs/gui-spec.md`](docs/gui-spec.md) — the GUI design spec
- [`docs/server-setup.md`](docs/server-setup.md) — running on a remote server (advanced)

## License

MIT — see [LICENSE](LICENSE).

## Contact

Author: Thea Borch · [thea@theborch.com](mailto:thea@theborch.com)
