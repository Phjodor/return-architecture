# Configuration

This doc covers the per-agent `config.toml` fields most users do *not* need to touch, and the asymmetries between providers that explain why some GUI settings appear and disappear.

## Per-agent config location

Every agent has a `config.toml` at:

```
~/return-architecture/agents/<slug>/config.toml
```

The GUI's **Identity** page edits the most commonly tuned fields. Anything not exposed in the GUI you can still edit by hand — the runtime reads the file fresh on each start.

## Provider asymmetries you may notice

### `max_tokens`

This sets the per-turn cap on how much the model is allowed to generate.

- **Anthropic**: the API *requires* a value. The GUI shows the field. Default `4096`, which is a sensible cap for chat. Raise it only if the agent regularly hits the cap on legitimate replies (you'll see truncated responses); lowering it forces the agent to be more terse.
- **OpenAI**: the API *does not* require it. The GUI hides the field — the runtime relies on OpenAI's model defaults instead. This is intentional: OpenAI renamed the parameter (`max_tokens` → `max_completion_tokens`) for the gpt-5 / o-series generation, and we'd rather not send a field that has a moving name.

If you want a hard cap on OpenAI generation length, you can set it manually in `config.toml` and the runtime will respect it — but the current OpenAI provider does not forward it to the API. (Mentioned for completeness; treat this as a future-work knob, not a supported feature today.)

### `temperature`

Optional on both providers. Some newer OpenAI models (reasoning models, certain gpt-5 variants) reject any explicit temperature and only accept the default — the GUI's **Set temperature explicitly** checkbox lets you leave it unset for those models. When in doubt, leave it off and use **Test connection** to verify.

## Knobs you can ignore

These exist in `config.toml` but you almost never need to touch them:

- `behavior.max_self_scheduled_jobs_per_day` — cap on how many times the agent can wake itself in a day. Default 5. Prevents runaway scheduling.
- `artifact_exchange.agent_max_tokens` / `mediator_max_tokens` — output caps for the three-call artifact ritual. Defaults are tuned; only change if you've watched several exchanges and have a reason.

## Where the friction lives

By design, Return Architecture exposes fewer settings than a typical AI tool. The GUI shows what you'll plausibly want to change; everything else lives in the TOML for the rare case you really mean it. If you find yourself wanting a setting that isn't in the GUI, that's worth flagging — usually it means we've hidden something we shouldn't have, or the default isn't right.
