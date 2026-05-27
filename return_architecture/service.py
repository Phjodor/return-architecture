"""Service control for the background daemon.

Manages a per-agent service that runs `return-architecture daemon <slug>`
in the background, auto-starts at login, and respawns on crash.

Two backends:

- macOS: launchd. Per-agent plist in `~/Library/LaunchAgents/`,
  controlled via `launchctl`.
- Linux: systemd user units. Per-agent unit file in
  `~/.config/systemd/user/`, controlled via `systemctl --user`.

Public functions (`install`, `restart`, `uninstall`, `status`,
`tail_logs`) dispatch to the right backend by platform.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from return_architecture import paths


# ── Shared types & helpers ────────────────────────────────────────────────

@dataclass
class ServiceStatus:
    label: str
    service_file_path: Path
    service_file_exists: bool
    loaded: bool
    pid: int | None
    raw_output: str


def _executable_path() -> Path:
    """Path to the return-architecture binary in the active venv."""
    return Path(sys.executable).parent / "return-architecture"


def stdout_log(slug: str) -> Path:
    return paths.agent_logs_dir(slug) / "daemon-stdout.log"


def stderr_log(slug: str) -> Path:
    return paths.agent_logs_dir(slug) / "daemon-stderr.log"


def _unsupported() -> RuntimeError:
    return RuntimeError(
        f"service commands are not supported on {platform.system()}. "
        "Supported: macOS (launchd), Linux (systemd user units)."
    )


# ── Public dispatch ───────────────────────────────────────────────────────

def install(slug: str) -> Path:
    system = platform.system()
    if system == "Darwin":
        return _macos_install(slug)
    if system == "Linux":
        return _linux_install(slug)
    raise _unsupported()


def restart(slug: str) -> None:
    system = platform.system()
    if system == "Darwin":
        return _macos_restart(slug)
    if system == "Linux":
        return _linux_restart(slug)
    raise _unsupported()


def uninstall(slug: str) -> None:
    system = platform.system()
    if system == "Darwin":
        return _macos_uninstall(slug)
    if system == "Linux":
        return _linux_uninstall(slug)
    raise _unsupported()


def status(slug: str) -> ServiceStatus:
    system = platform.system()
    if system == "Darwin":
        return _macos_status(slug)
    if system == "Linux":
        return _linux_status(slug)
    raise _unsupported()


def is_loaded(slug: str) -> bool:
    system = platform.system()
    if system == "Darwin":
        return _macos_is_loaded(slug)
    if system == "Linux":
        return _linux_is_loaded(slug)
    return False


def tail_logs(slug: str, lines: int = 40) -> tuple[str, str]:
    """Return (stdout_tail, stderr_tail). Platform-agnostic — both
    backends write to the same agent log paths."""
    return _tail_file(stdout_log(slug), lines), _tail_file(stderr_log(slug), lines)


def _tail_file(path: Path, lines: int) -> str:
    if not path.exists():
        return ""
    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(path)],
            capture_output=True, text=True, check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


# ── macOS / launchd backend ───────────────────────────────────────────────

def _macos_label(slug: str) -> str:
    return f"com.returnarchitecture.{slug}.daemon"


def _macos_plist_path(slug: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_macos_label(slug)}.plist"


def _macos_install(slug: str) -> Path:
    exe = _executable_path()
    if not exe.exists():
        raise FileNotFoundError(
            f"Can't find return-architecture executable at {exe}. "
            f"Run `uv sync` in the source repo, then try again."
        )

    paths.agent_logs_dir(slug).mkdir(parents=True, exist_ok=True)
    plist_path = _macos_plist_path(slug)
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    plist_content = _build_plist(
        label=_macos_label(slug),
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
    if _macos_is_loaded(slug):
        _macos_try_bootout(slug)
        import time
        for _ in range(25):
            if not _macos_is_loaded(slug):
                break
            time.sleep(0.2)

    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"launchctl bootstrap failed (exit {result.returncode})"
            + (f": {msg}" if msg else "")
        )
    return plist_path


def _macos_is_loaded(slug: str) -> bool:
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{_macos_label(slug)}"],
        capture_output=True,
    )
    return proc.returncode == 0


def _macos_restart(slug: str) -> None:
    if not _macos_is_loaded(slug):
        _macos_install(slug)
        return
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{_macos_label(slug)}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"launchctl kickstart failed (exit {result.returncode})"
            + (f": {msg}" if msg else "")
        )


def _macos_uninstall(slug: str) -> None:
    _macos_try_bootout(slug)
    plist_path = _macos_plist_path(slug)
    if plist_path.exists():
        plist_path.unlink()


def _macos_status(slug: str) -> ServiceStatus:
    plist_path = _macos_plist_path(slug)
    label = _macos_label(slug)
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        capture_output=True, text=True,
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
        service_file_path=plist_path,
        service_file_exists=plist_path.exists(),
        loaded=loaded,
        pid=pid,
        raw_output=(proc.stdout if loaded else proc.stderr),
    )


def _macos_try_bootout(slug: str) -> None:
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{_macos_label(slug)}"],
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


# ── Linux / systemd-user backend ──────────────────────────────────────────

def _linux_unit_name(slug: str) -> str:
    return f"return-architecture-{slug}.service"


def _linux_unit_path(slug: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / _linux_unit_name(slug)


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"systemctl --user {' '.join(args)} failed (exit {result.returncode})"
            + (f": {msg}" if msg else "")
        )
    return result


def _linux_install(slug: str) -> Path:
    exe = _executable_path()
    if not exe.exists():
        raise FileNotFoundError(
            f"Can't find return-architecture executable at {exe}. "
            f"Run `uv sync` in the source repo, then try again."
        )

    paths.agent_logs_dir(slug).mkdir(parents=True, exist_ok=True)
    unit_path = _linux_unit_path(slug)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(
        _build_systemd_unit(
            slug=slug,
            executable=str(exe),
            install_root=str(paths.install_root()),
            stdout_path=str(stdout_log(slug)),
            stderr_path=str(stderr_log(slug)),
        ),
        encoding="utf-8",
    )

    unit = _linux_unit_name(slug)
    _systemctl("daemon-reload")
    _systemctl("enable", unit)
    if _linux_is_active(slug):
        _systemctl("restart", unit)
    else:
        _systemctl("start", unit)
    return unit_path


def _linux_is_active(slug: str) -> bool:
    proc = _systemctl("is-active", _linux_unit_name(slug), check=False)
    return proc.stdout.strip() == "active"


def _linux_is_loaded(slug: str) -> bool:
    return _linux_is_active(slug)


def _linux_restart(slug: str) -> None:
    unit = _linux_unit_name(slug)
    if not _linux_unit_path(slug).exists():
        _linux_install(slug)
        return
    _systemctl("restart", unit)


def _linux_uninstall(slug: str) -> None:
    unit = _linux_unit_name(slug)
    # Best-effort stop and disable; ignore failures (e.g. unit not loaded).
    _systemctl("stop", unit, check=False)
    _systemctl("disable", unit, check=False)
    unit_path = _linux_unit_path(slug)
    if unit_path.exists():
        unit_path.unlink()
    _systemctl("daemon-reload", check=False)


def _linux_status(slug: str) -> ServiceStatus:
    unit = _linux_unit_name(slug)
    unit_path = _linux_unit_path(slug)
    proc = _systemctl(
        "show", unit, "--no-page",
        "--property=ActiveState,MainPID,SubState",
        check=False,
    )
    props: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    active = props.get("ActiveState") == "active"
    pid_raw = props.get("MainPID", "0")
    try:
        pid: int | None = int(pid_raw) if pid_raw and pid_raw != "0" else None
    except ValueError:
        pid = None
    return ServiceStatus(
        label=unit,
        service_file_path=unit_path,
        service_file_exists=unit_path.exists(),
        loaded=active,
        pid=pid,
        raw_output=proc.stdout,
    )


def _build_systemd_unit(
    *,
    slug: str,
    executable: str,
    install_root: str,
    stdout_path: str,
    stderr_path: str,
) -> str:
    return f"""[Unit]
Description=Return Architecture daemon for {slug}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=RA_INSTALL_ROOT={install_root}
Environment=PYTHONUNBUFFERED=1
ExecStart={executable} daemon {slug}
Restart=on-failure
RestartSec=60
StandardOutput=append:{stdout_path}
StandardError=append:{stderr_path}

[Install]
WantedBy=default.target
"""
