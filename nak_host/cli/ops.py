"""Server process operations for `python nebulonak.py`.

Mirrors NebulonMD's `nmd_host/cli` (plain uvicorn, PID file, port probe) —
no Textual TUI, no prompts. All paths come from Settings ([paths]/[server]).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _pid_from_file(pid_file: str) -> Optional[int]:
    try:
        return int(Path(pid_file).read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            return True
    except OSError:
        return False


def _cleanup_pid_file(pid_file: str) -> None:
    import contextlib

    with contextlib.suppress(OSError):
        Path(pid_file).unlink(missing_ok=True)


def describe(settings: Any = None) -> dict[str, Any]:
    """Current server state (never raises, never starts anything)."""
    from nak_host.utils.settings import load_settings

    s = settings or load_settings()
    pid = _pid_from_file(s.pid_file())
    return {"pid": pid, "pid_alive": bool(pid is not None and _pid_alive(pid)),
            "port_open": _port_open(s.serve_host, int(s.serve_port)),
            "host": s.serve_host, "port": int(s.serve_port),
            "pid_file": s.pid_file(), "logs_dir": s.logs_dir()}


def start(settings: Any = None, foreground: bool = False) -> bool:
    """Start the server (background daemon by default)."""
    from nak_host.utils.settings import load_settings

    s = settings or load_settings()
    pid = _pid_from_file(s.pid_file())
    if pid is not None and _pid_alive(pid):
        print(f"Server is already running (PID {pid}).")
        return False
    if pid is not None:
        print(f"Found stale PID file (PID {pid}). Removing it.")
        _cleanup_pid_file(s.pid_file())
    if _port_open(s.serve_host, int(s.serve_port)):
        print(f"Server is already listening on {s.serve_host}:{s.serve_port}.")
        return False
    logs = Path(s.logs_dir())
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = logs / f"nebulonak_{stamp}.log"
    cmd = [sys.executable, "-m", "uvicorn", "nak_host.api.main:app",
           "--host", str(s.serve_host), "--port", str(int(s.serve_port)),
           "--timeout-keep-alive", str(int(s.serve_keep_alive)),
           "--timeout-graceful-shutdown", str(int(s.serve_graceful_timeout)),
           "--log-level", str(s.serve_log_level or "info")]
    print(f"Starting NebulonAK on {s.serve_host}:{s.serve_port} ...")
    if foreground:
        print("Running in foreground. Press Ctrl+C to stop.")
        try:
            subprocess.run(cmd, cwd=s.home_path())
        except KeyboardInterrupt:
            print("\nShutting down server.")
        return True
    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Popen dups the fds; the parent handles close right after spawn.
    with open(log_file, "a", encoding="utf-8") as out:
        kwargs: dict[str, Any] = {"stdout": out, "stderr": out,
                                  "stdin": subprocess.DEVNULL,
                                  "cwd": s.home_path()}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(cmd, **kwargs)
    Path(s.pid_file()).write_text(str(process.pid), encoding="utf-8")
    print(f"Server started with PID {process.pid}.")
    print(f"Console: http://{s.serve_host}:{s.serve_port}/api/NebulonAK/dashboard/")
    return True


def stop(settings: Any = None, force: bool = False) -> bool:
    """Stop the server (PID file, or port scan with --force)."""
    import signal

    from nak_host.utils.settings import load_settings

    s = settings or load_settings()
    pid = _pid_from_file(s.pid_file())
    if pid is None:
        if not _port_open(s.serve_host, int(s.serve_port)):
            print("Server is not running.")
            return False
        if not force:
            print(f"PID file not found but {s.serve_host}:{s.serve_port} "
                  f"is listening (manual start?). Use --force to stop it.")
            return False
        print("Force option for manual starts is not implemented; "
              "stop the process directly.")
        return False
    if not _pid_alive(pid):
        print(f"Process with PID {pid} is not running. Cleaning up PID file.")
        _cleanup_pid_file(s.pid_file())
        return True
    print(f"Stopping NebulonAK (PID {pid})...")
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError as exc:
        print(f"Failed to signal PID {pid}: {exc}")
        return False
    for _ in range(10):
        time.sleep(1.0 if force else 0.5)
        if not _pid_alive(pid):
            break
    if not _pid_alive(pid):
        print("Server stopped successfully.")
        _cleanup_pid_file(s.pid_file())
        return True
    print(f"Failed to stop server. PID {pid} is still alive.")
    return False


def restart(settings: Any = None, foreground: bool = False,
            force: bool = False) -> bool:
    """Stop (if running) then start."""
    from nak_host.utils.settings import load_settings

    s = settings or load_settings()
    if _pid_from_file(s.pid_file()) is not None \
            or _port_open(s.serve_host, int(s.serve_port)):
        stop(s, force=force)
        time.sleep(2)
    return start(s, foreground=foreground)
