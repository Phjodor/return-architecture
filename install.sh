#!/bin/sh
#
# Return Architecture installer.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/Theapolar/return-architecture/main/install.sh | sh
#
# What this script does:
#   1. Installs uv (a Python manager + fast package installer) if not present
#   2. Uses `uv tool install` to install Return Architecture as an isolated CLI tool
#   3. Verifies the install and prints next steps
#
# Re-running is safe — the script forces a reinstall, which is also how you upgrade.
#
# Supported: macOS (full support, including the background service)
#            Linux (the CLI + daemon; background service requires manual systemd setup)

set -e

REPO_URL="git+https://github.com/Theapolar/return-architecture"
INSTALL_NAME="return-architecture"

# ── Helpers ──────────────────────────────────────────────────────────────
banner() {
    printf "\n"
    printf "════════════════════════════════════════════════════════════\n"
    printf "  Return Architecture installer\n"
    printf "════════════════════════════════════════════════════════════\n"
    printf "\n"
}
step() { printf "\n→ %s\n" "$*"; }
ok()   { printf "  ✓ %s\n" "$*"; }
err()  { printf "\n✗ %s\n" "$*" >&2; }

banner

# ── 1. Install uv if missing ─────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    step "Installing uv (handles Python + package installation)..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        err "uv installation failed. See https://docs.astral.sh/uv/ for manual install."
        exit 1
    fi
    # uv installs to ~/.local/bin and writes an env file we can source to update PATH
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1090
        . "$HOME/.local/bin/env"
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
    err "uv is still not on PATH after install."
    err "Open a new terminal and re-run this script, or install uv manually:"
    err "  https://docs.astral.sh/uv/"
    exit 1
fi

ok "uv $(uv --version | awk '{print $2}') is available"

# ── 2. Install Return Architecture ───────────────────────────────────────
step "Installing Return Architecture from GitHub..."
printf "  (this can take 1-2 minutes — large dependencies like chromadb)\n"

if ! uv tool install --force --from "$REPO_URL" "$INSTALL_NAME" >/dev/null; then
    err "Install failed. Possible causes:"
    err "  • No network connectivity"
    err "  • Python build tools missing (on Linux: apt install build-essential)"
    err "Try running with verbose output:"
    err "  uv tool install --force --from $REPO_URL $INSTALL_NAME"
    exit 1
fi

# Make sure ~/.local/bin is registered on PATH for future shells
uv tool update-shell >/dev/null 2>&1 || true
export PATH="$HOME/.local/bin:$PATH"

# ── 3. Verify ────────────────────────────────────────────────────────────
if ! command -v return-architecture >/dev/null 2>&1; then
    err "Installation succeeded but 'return-architecture' is not on PATH."
    err "Try opening a new terminal, or run: source ~/.local/bin/env"
    exit 1
fi
ok "$(return-architecture --help 2>/dev/null | head -1 || echo 'return-architecture is installed')"

# ── 4. Next steps ────────────────────────────────────────────────────────
printf "\n"
printf "════════════════════════════════════════════════════════════\n"
printf "  ✓ Return Architecture is installed.\n"
printf "════════════════════════════════════════════════════════════\n"
printf "\n"
printf "Next steps:\n"
printf "\n"
printf "  1. Open a new terminal window so 'return-architecture' is on your PATH.\n"
printf "     (Or in this terminal: source ~/.local/bin/env)\n"
printf "\n"
printf "  2. Launch the setup wizard:\n"
printf "       return-architecture gui\n"
printf "\n"
printf "  3. Walk through the 5-minute first-run wizard.\n"
printf "     You'll need an Anthropic or OpenAI API key.\n"
printf "\n"
printf "Docs: https://github.com/Theapolar/return-architecture\n"
printf "\n"
