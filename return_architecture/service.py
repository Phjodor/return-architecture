"""macOS launchd service control for the daemon.

Writes a per-agent plist to ~/Library/LaunchAgents/ and uses launchctl to
load and unload it. The plist points at the return-architecture binary in
the current venv, sets RA_INSTALL_ROOT as an env var, and captures stdout
and stderr to log files in the agent's folder.

macOS only. Linux/Windows are detected and refused with a clear message.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from return_architecture import paths


def _check_macos() -> None:
    if platform.system() != "Darwin":
        raise RuntimeError(
            "service commands are macOS-only (uses launchd). "
            "Linux support via systemd user units will arrive later."
        )


def _label(slug: str) -> str:
    return f"com.returnarchitecture.{slug}.daemon"


def _plist_path(slug: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_label(slug)}.plist"


def _executable_path() -> Path:
    """Return path to the return-architecture binary in the active venv."""
    return Path(sys.executable).parent / "return-architecture"


def stdout_log(slug: str) -> Path:
    return paths.agent_logs_dir(slug) / "daemon-stdout.log"


def stderr_log(slug: str) -> Path:
    return paths.agent_logs_dir(slug) / "daemon-stderr.log"


@dataclass
class ServiceStatus:
    label: str
    plist_path: Path
    plist_exists: bool
    loaded: bool
    pid: int | None
    raw_output: str


def install(slug: str) -> Path:
    _check_macos()
    exe = _executable_path()
    if not exe.exists():
        raise FileNotFoundError(
            f"Can't find return-architecture executable at {exe}. "
            f"Run `uv sync` in the source repo, then try again."
        )

    paths.agent_logs_dir(slug).mkdir(parents=True, exist_ok=True)
    plist_path = _plist_path(slug)
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    plist_content = _build_plist(
        label=_label(slug),
        executable=str(exe),
        slug=slug,
        install_root=str(paths.install_root()),
        stdout_path=str(stdout_log(slug)),
        stderr_path=str(stderr_log(slug)),
    )
    plist_path.write_text(plist_content, encoding="utf-8")

    # If a previous version is already loaded, unload it first and wait for
    # launchd to actually release the service — bootstrap can fail with EIO
    # if the previous instance is still being torn down.
    if is_loaded(slug):
        _try_bootout(slug)
        import time
        for _ in range(25):
            if not is_loaded(slug):
                break
            time.sleep(0.2)

    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"launchctl bootstrap failed (exit {result.returncode})"
            + (f": {msg}" if msg else "")
        )
    return plist_path


def is_loaded(slug: str) -> bool:
    """Cheaply check whether the service is currently registered with launchd."""
    if platform.system() != "Darwin":
        return False
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{_label(slug)}"],
        capture_output=True,
    )
    return proc.returncode == 0


def restart(slug: str) -> None:
    """Restart the running daemon in place.

    Uses `launchctl kickstart -k`, which terminates the current daemon
    process and lets launchd respawn it with the same plist. The new
    daemon re-reads config files on startup, so this is the right call
    after editing agent config / system prompt / schedules / tools.

    Falls back to install() if the service isn't currently loaded.
    """
    _check_macos()
    if not is_loaded(slug):
        install(slug)
        return
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{_label(slug)}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"launchctl kickstart failed (exit {result.returncode})"
            + (f": {msg}" if msg else "")
        )


def uninstall(slug: str) -> None:
    _check_macos()
    _try_bootout(slug)
    plist_path = _plist_path(slug)
    if plist_path.exists():
        plist_path.unlink()


def status(slug: str) -> ServiceStatus:
    _check_macos()
    plist_path = _plist_path(slug)
    label = _label(slug)
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        capture_output=True,
        text=True,
    )
    loaded = proc.returncode == 0
    pid: int | None = None
    if loaded:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid ="):
                try:
                    pid = int(line.split("=")[1].strip())
                except ValueError:
                    pid = None
                break
    return ServiceStatus(
        label=label,
        plist_path=plist_path,
        plist_exists=plist_path.exists(),
        loaded=loaded,
        pid=pid,
        raw_output=(proc.stdout if loaded else proc.stderr),
    )


def tail_logs(slug: str, lines: int = 40) -> tuple[str, str]:
    """Return (stdout_tail, stderr_tail)."""
    out = _tail_file(stdout_log(slug), lines)
    err = _tail_file(stderr_log(slug), lines)
    return out, err


def _tail_file(path: Path, lines: int) -> str:
    if not path.exists():
        return ""
    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


def _try_bootout(slug: str) -> None:
    """Best-effort unload; ignore errors if the service isn't loaded."""
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{_label(slug)}"],
        capture_output=True,
    )


def _build_plist(
    *,
    label: str,
    executable: str,
    slug: str,
    install_root: str,
    stdout_path: str,
    stderr_path: str,
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{executable}</string>
        <string>daemon</string>
        <string>{slug}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>RA_INSTALL_ROOT</key>
        <string>{install_root}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>{stdout_path}</string>
    <key>StandardErrorPath</key>
    <string>{stderr_path}</string>
</dict>
</plist>
"""
