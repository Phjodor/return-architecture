# Running on a remote server

Return Architecture is local-first by design. Most people should run it on their personal machine and not think about servers. But if you specifically want always-on operation without your laptop running — and you're comfortable with SSH and Linux — there are three reasonable paths.

> **Never expose the GUI directly to the internet.** The Streamlit control panel has no authentication. It binds to `127.0.0.1` by default — keep it that way. For remote access, use SSH tunnelling or Tailscale. Both are described below.

## What changes on a remote server

The system runs almost identically. The pieces that change:

- **Service management.** macOS uses launchd; Linux servers need a systemd user unit. Full Linux support is on the roadmap; until it lands, you start the daemon manually (`return-architecture daemon <slug>`) inside a terminal multiplexer (tmux, screen) or write your own systemd unit by hand.
- **GUI access.** The GUI runs on `localhost:8501` of the server. To use it from your laptop, you SSH-tunnel that port to your local machine — described below.
- **Filesystem MCP scope.** If you point the agent at a "notes" folder, that folder lives on the server. To make local edits visible, sync notes to the server (Syncthing, rclone, or a mounted Dropbox/Drive folder).
- **Telegram.** No change — the bot makes outbound long-poll requests; no inbound network needed. A server behind NAT or with no public IP works fine.

## Option 1: home server with Tailscale (recommended)

The cleanest path for someone who has spare hardware at home (Raspberry Pi, old Mac mini, old laptop, NAS that can run Docker).

1. Install Return Architecture on the server as you would locally.
2. Install [Tailscale](https://tailscale.com/) on both the server and your laptop. It's free for personal use and creates a private virtual network just for your devices — no port forwarding, no public exposure.
3. Once both devices are in your Tailscale network, you can SSH into the server using its Tailscale name (`ssh you@my-server`).
4. To use the GUI from your laptop, set up an SSH tunnel that forwards the server's localhost:8501 to your laptop's localhost:8501:
   ```bash
   ssh -L 8501:localhost:8501 you@my-server
   ```
   Then on the server, run `return-architecture gui`. Open `http://localhost:8501` in your laptop's browser. The browser talks to your laptop's localhost, which is tunnelled over SSH to the server's localhost. Nothing is exposed to the public internet.

Data lives entirely on hardware you own.

## Option 2: cheap VPS (Hetzner, DigitalOcean, Linode)

For people who don't want home hardware. Typically $5–10 per month.

1. Spin up an Ubuntu or Debian instance. Pick a small size; the agent doesn't need much memory.
2. SSH in. Install Python 3.11–3.13, `pipx`, then Return Architecture as in the README.
3. Add your API keys, create your agent. Set up Telegram so the agent can reach you from anywhere.
4. Start the daemon:
   ```bash
   return-architecture daemon <slug>
   ```
   ... and either keep an SSH session open, run it inside tmux/screen, or write your own systemd user unit (a simple `~/.config/systemd/user/return-architecture.service` will do).
5. Use SSH tunnelling (same `ssh -L 8501:localhost:8501` pattern) when you want to open the GUI from your laptop.

Encrypted backups of `~/return-architecture/` are recommended — that folder contains your secrets, memory, and items.

## Option 3: Docker

Not yet shipped, but a future addition. A `Dockerfile` and a `docker-compose.yml` would mount your data directory as a volume and expose nothing publicly. Useful for running on a NAS or in any container host. If you want this and can help test it, open an issue on the repo.

## A few honest notes

- **The GUI is not designed to be hardened.** No login, no rate limiting, no audit log. Treat it like a control panel that should only ever be reachable from inside your trusted network (Tailscale, SSH tunnel, localhost).
- **Server reliability matters less than you think for this use case.** The agent isn't real-time. If the server is down for ten minutes, Telegram will just buffer your messages and they'll arrive when the daemon comes back. Cheap hardware and free home internet are fine.
- **Power cycling is fine.** Memory (Chroma), items, schedules, secrets — all persistent on disk. The agent picks up where it left off.
