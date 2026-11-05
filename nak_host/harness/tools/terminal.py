"""Terminal tool — sandboxed to a session root, uv-first.

Contract: fn(args: dict) -> JSON string. Every call takes:
  in:  {"command": "<str | list, required>", "workdir": "<dir inside root, optional>",
        "timeout": "<seconds, optional, default 60, max 120>",
        "root": "<sandbox dir, optional>"}
  out: '{"ok": true, ...}' or '{"ok": false, "error": ...}'

Policy (matches file.py sandbox):
- workdir must resolve inside root via safe_join (no .. / absolute /
  symlink escapes); .env, *.key/*.pem and secrets/ + .git/ denied.
- Runs with cwd=resolved workdir, shell=False (no shell injection).
- Python libs MUST be installed via uv into the session, never .venv:
    uv pip install --target .libs pypdf
    PYTHONPATH=.libs python work/extract.py inbox/a.pdf out/a.txt
  The tool auto-sets PYTHONPATH=<root>/.libs so worker can omit it,
  and sets UV_CACHE_DIR=<root>/.uv-cache to keep home clean.
- Allowlist binaries (fail-closed): uv, python, python3, ls, cat, head,
  tail, wc, echo, mkdir, touch, pdfinfo, pdftotext. Anything else is
  refused — extend ALLOW_BINARIES deliberately, never blindly.
- Denylist flags even for allowed binaries: sudo/su/doas, rm -rf /,
  mkfs, dd, shutdown, reboot, chmod / chown escapes are rejected by
  string scan. No interactive stdin (input=/dev/null).
- Output capped (12k chars) + timeout enforced. Always returns a
  string, never raises — engine-safe.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess

from pathlib import Path
from typing import Any, List

try:
    from ..engine.sandbox import default_workspace, safe_join
except Exception:  # pragma: no cover — direct-path import fallback
    from nak_host.harness.engine.sandbox import default_workspace, safe_join  # type: ignore

from nak_host.utils.constant import (
    TERMINAL_ALLOW_BINARIES as ALLOW_BINARIES,
    TERMINAL_ALLOW_GIT_SUBCOMMANDS as ALLOW_GIT_SUBCOMMANDS,
    TERMINAL_DEFAULT_TIMEOUT as DEFAULT_TIMEOUT,
    TERMINAL_DENY_RM_PATTERNS as DENY_RM_PATTERNS,
    TERMINAL_DENY_SUBSTRINGS as DENY_SUBSTRINGS,
    TERMINAL_MAX_OUTPUT as MAX_OUTPUT,
    TERMINAL_MAX_TIMEOUT as MAX_TIMEOUT,
)


def _root(data: dict) -> str:
    return (str(data.get("root", "")).strip()
            or os.environ.get("NAK_WORKSPACE", "").strip()
            or str(default_workspace()))


def _as_argv(command: Any) -> List[str]:
    if isinstance(command, (list, tuple)):
        argv = [str(c) for c in command if str(c).strip() != ""]
        return argv
    if isinstance(command, str):
        if not command.strip():
            return []
        try:
            return shlex.split(command, posix=True)
        except ValueError as exc:
            raise ValueError(f"unparseable command: {exc}") from exc
    return []


def _refuse_reason(argv: List[str]) -> str | None:
    if not argv:
        return "missing command: worker must pass a command string or arg list"
    binary = Path(argv[0]).name.strip()
    if binary not in ALLOW_BINARIES:
        return (f"binary {binary!r} not allowed "
                f"(allow: {sorted(ALLOW_BINARIES)} — install libs via "
                f"'uv pip install --target .libs <pkg>')")
    joined = " ".join(argv)
    low = f" {joined.lower()} "
    for bad in DENY_SUBSTRINGS:
        if bad.lower() in low:
            return f"refused: forbidden pattern {bad!r} in command"
    for pat in DENY_RM_PATTERNS:
        if pat in joined:
            return f"refused: dangerous pattern {pat!r}"
    # git is read-only here: only inspection subcommands pass.
    if binary == "git":
        sub = next((a for a in argv[1:] if not a.startswith("-")), "")
        if sub and sub not in ALLOW_GIT_SUBCOMMANDS:
            return (f"refused: git subcommand {sub!r} is not read-only "
                    f"(allow: {sorted(ALLOW_GIT_SUBCOMMANDS)})")
    # uv must stay session-local: refuse installs without --target/.libs
    # or --with (ephemeral). Reads (uv --version, uv pip list) pass.
    if binary == "uv" and "install" in argv:
        if "--target" not in argv and "--with" not in argv:
            return ("refused: uv installs must be session-local, e.g. "
                    "'uv pip install --target .libs pypdf'")
        for i, tok in enumerate(argv):
            if tok == "--target" and i + 1 < len(argv):
                tgt = argv[i + 1]
                if tgt.startswith("/") or tgt.startswith("~") or ".." in tgt:
                    return f"refused: --target must stay inside session (got {tgt!r})"
    return None


def run_terminal(args: dict) -> str:
    """Execute one sandboxed command (cwd inside root)."""
    data = args or {}
    try:
        argv = _as_argv(data.get("command"))
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    refused = _refuse_reason(argv)
    if refused:
        return json.dumps({"ok": False, "error": refused})

    workdir_raw = str(data.get("workdir", "")).strip() or "."
    ok, resolved = safe_join(_root(data), workdir_raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    cwd = Path(resolved)
    try:
        cwd.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})

    try:
        timeout = float(data.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = min(max(timeout, 1.0), float(MAX_TIMEOUT))

    root = str(Path(_root(data)).expanduser())
    libs = str((Path(root) / ".libs").resolve())
    uvcache = str((Path(root) / ".uv-cache").resolve())
    env = dict(os.environ)
    prev_py = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = libs + (os.pathsep + prev_py if prev_py else "")
    env["UV_CACHE_DIR"] = uvcache
    # uv lives in ~/.local/bin (WSL) / ~/.cargo/bin — both are often
    # missing from non-interactive PATH. Prepend them so shutil.which
    # + subprocess find uv without any global install.
    import shutil
    home = str(Path.home())
    extra_bins = [os.path.join(home, ".local", "bin"),
                  os.path.join(home, ".cargo", "bin")]
    env["PATH"] = os.pathsep.join(extra_bins + [env.get("PATH", "")])
    # Never leak interactive/auth env into child.
    env.pop("NAK_AUTH_TOKEN", None)
    resolved_bin = shutil.which(argv[0], path=env["PATH"])
    if resolved_bin is None:
        return json.dumps({"ok": False,
                           "error": f"binary {argv[0]!r} not found on PATH "
                                    f"(tried ~/.local/bin, ~/.cargo/bin — install uv via "
                                    f"'curl -LsSf astral.sh/uv.sh | sh')"})

    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), env=env, timeout=timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", shell=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": f"timeout after {timeout:g}s",
                           "command": argv, "cwd": str(cwd)})
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    truncated = False
    if len(stdout) + len(stderr) > MAX_OUTPUT:
        budget = MAX_OUTPUT - len("[…truncated] ")
        stdout = (stdout + "\n---stderr---\n" + stderr)[:budget] + "\n[…truncated]"
        stderr = ""
        truncated = True
    return json.dumps({"ok": proc.returncode == 0,
                       "exit_code": proc.returncode,
                       "command": argv, "cwd": str(cwd),
                       "stdout": stdout, "stderr": stderr,
                       "truncated": truncated,
                       **({"error": (stderr or stdout)[:300]}
                          if proc.returncode != 0 else {})})


def terminal(args: dict) -> str:
    """Single terminal tool: route args.action (exec|run|uv|python) to run_terminal."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower() or "exec"
    if action in ("exec", "run", "sh", "uv", "python"):
        return run_terminal(data)
    return json.dumps({"ok": False,
                        "error": f"bad action {str(data.get('action', ''))!r}: want exec|run"})
