"""nak.plugins.tool.exec_tools -- execution tools for the harness (Phase 2).

    shell        run a shell command (cwd sandboxed, timeout, truncated output)
    python_exec  run `python -c <code>` in-process via subprocess (same sandbox)
    run_test     run pytest (or a custom command) and return pass/fail + logs

Safety:
    - Every call runs with a timeout (default 30s, max 120s).
    - cwd defaults to the harness workspace project dir; `..` escapes are
      rejected via workspace.resolve_in_workspace when a workspace is given.
    - Catastrophic shapes (`rm -rf /`, `~`, `$HOME`, `--no-preserve-root`,
      fork bombs, `mkfs`, `dd ... of=/dev/`) are REFUSED before execution
      (ordinary cleanup like `rm -rf build` still runs).
    - Output is truncated (tail) so a noisy test run cannot blow up the LLM
      transcript. Full logs belong in workspace/logs/ (written by verifier).
    - Policy gating (ask/allow_once/...) happens in nak.harness.executor,
      NOT here — these functions assume approval was already granted.

All executors return JSON-serializable dicts; execute_exec_tool() wraps them
as JSON strings with {"error": ...} on failure (loop-safe).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 120
_MAX_OUTPUT = 12000

# Catastrophic shell shapes: refused BEFORE execution (defense in depth on top
# of policy approval). Deliberately narrow — legit work like `make clean`
# (`rm -rf build`) or `rm -f *.o` must keep working.
_DESTRUCTIVE_PATTERNS = (
    r"\brm\s+.*--no-preserve-root\b",
    r"\brm\s+(-[a-z]*r[a-z]*\s+)+/\s*(;|$|&&|\|\|)",
    r"\brm\s+(-[a-z]*r[a-z]*\s+)+~/?\s*(;|$|&&|\|\|)",
    r"\brm\s+(-[a-z]*r[a-z]*\s+)+\$HOME/?\s*(;|$|&&|\|\|)",
    r"\brm\s+(-[a-z]*r[a-z]*\s+)/\*",
    r":\(\)\s*\{\s*:\|\:&\s*\}\s*;",  # fork bomb
    r"\bmkfs[\s.]",  # mkfs / mkfs.ext4 ...
    r"\bdd\s+.*\bof=/dev/",
)


def destructive_command_reason(command: str) -> Optional[str]:
    """Return a refusal reason if `command` is catastrophically destructive, else None.

    Pure function (offline-testable). Catches machine-killers regardless of
    cwd: `rm -rf /|~|$HOME|/*`, `--no-preserve-root`, fork bombs, `mkfs`,
    `dd ... of=/dev/*`. Workspace-relative containment (`rm` escaping its cwd)
    is enforced separately in shell(), which knows the sandbox dir.
    """
    import re as _re

    text = (command or "").strip()
    for pat in _DESTRUCTIVE_PATTERNS:
        if _re.search(pat, text):
            return (f"refused destructive command (matched {pat[:40]}…): "
                    "run it yourself in a terminal if you really mean it")
    return None


def _rm_escapes_workspace(command: str, cwd: Optional[str | Path]) -> Optional[str]:
    """Refuse recursive rm whose target escapes `cwd` (or hits / and home).

    Only applies when a sandbox dir is given; without cwd, containment is the
    policy layer's job (approval prompt). Returns refusal reason or None.
    """
    import os as _os
    import re as _re
    import shlex as _shlex

    if cwd is None:
        return None
    try:
        base = Path(cwd).expanduser().resolve()
        home = Path.home().resolve()
        root = Path("/").resolve()
    except Exception:
        return None
    for segment in _re.split(r";|&&|\|\|", command or ""):
        m = _re.match(r"(?:sudo\s+)?rm\s+(.*)$", segment.strip())
        if not m:
            continue
        try:
            parts = _shlex.split(m.group(1), posix=True)
        except ValueError:
            continue
        if not any("r" in p for p in parts if p.startswith("-")):
            continue  # non-recursive rm: ordinary file ops, policy-gated
        for op in (p for p in parts if not p.startswith("-")):
            expanded = _os.path.expanduser(_os.path.expandvars(op))
            target = (Path(expanded).resolve() if _os.path.isabs(expanded)
                      else (base / expanded).resolve())
            if target in (root, home):
                return (f"refused: rm target {op!r} resolves to {target} "
                        "— run it yourself in a terminal if you really mean it")
            try:
                target.relative_to(base)
            except ValueError:
                return (f"refused: rm target {op!r} escapes workspace {base}")
    return None


def _run(
    cmd: List[str] | str,
    *,
    cwd: Optional[str | Path] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    shell: bool = False,
) -> Dict[str, Any]:
    timeout = max(1, min(int(timeout or _DEFAULT_TIMEOUT), _MAX_TIMEOUT))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            shell=shell,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        return {
            "ok": False,
            "returncode": None,
            "timeout": True,
            "command": cmd if isinstance(cmd, str) else " ".join(cmd),
            "output": str(out)[-_MAX_OUTPUT:],
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "timeout": False,
            "command": cmd if isinstance(cmd, str) else " ".join(cmd),
            "error": f"command not found: {exc}",
        }
    output = (proc.stdout or "") + (proc.stderr or "")
    truncated = len(output) > _MAX_OUTPUT
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "timeout": False,
        "command": cmd if isinstance(cmd, str) else " ".join(cmd),
        "output": output[-_MAX_OUTPUT:],
        "truncated": truncated,
    }


def shell(
    command: str,
    *,
    cwd: Optional[str | Path] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Run a shell command string. cwd must already be sandbox-resolved."""
    if not command or not command.strip():
        raise ValueError("command must be non-empty")
    refused = destructive_command_reason(command) or _rm_escapes_workspace(command, cwd)
    if refused:
        return {
            "ok": False,
            "returncode": None,
            "timeout": False,
            "command": command.strip()[:300],
            "output": "",
            "truncated": False,
            "error": refused,
        }
    return _run(command.strip(), cwd=cwd, timeout=timeout, shell=True)


def python_exec(
    code: str,
    *,
    cwd: Optional[str | Path] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Run `python -c <code>` as a subprocess (isolated, timeout-guarded)."""
    if not code or not code.strip():
        raise ValueError("code must be non-empty")
    return _run([sys.executable, "-c", code], cwd=cwd, timeout=timeout)


def run_test(
    target: str = ".",
    *,
    cwd: Optional[str | Path] = None,
    timeout: int = 60,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run `python -m pytest <target>` and summarise pass/fail.

    pytest is a runtime dependency (same interpreter runs it). When it is
    missing, this returns a clear failure with an install hint instead of an
    opaque `No module named pytest` traceback.
    """
    import importlib.util

    if importlib.util.find_spec("pytest") is None:
        cmd = " ".join([sys.executable, "-m", "pytest", target or "."])
        return {
            "ok": False,
            "returncode": None,
            "timeout": False,
            "command": cmd,
            "output": "pytest is not installed in this Python "
                      f"({sys.executable}). Install it with: "
                      f"{sys.executable} -m pip install pytest",
            "truncated": False,
            "passed": False,
            "summary": ["pytest is not installed"],
        }
    cmd = [sys.executable, "-m", "pytest", target or "."]
    if extra_args:
        cmd.extend(extra_args)
    res = _run(cmd, cwd=cwd, timeout=timeout)
    out = str(res.get("output") or "")
    if "No module named pytest" in out:
        # subprocess interpreter differs from ours — same hint applies
        res["output"] = out + (
            "\nHINT: pytest is not installed for the test interpreter. "
            "Install it with: python -m pip install pytest")
    # cheap summary: pytest prints "N passed" / "N failed"
    res["passed"] = res.get("ok") is True
    res["summary"] = out.strip().splitlines()[-5:] if out.strip() else []
    return res


def detect_build_command(cwd: Optional[str | Path] = None) -> Optional[str]:
    """Guess the project's build command from files in `cwd` (or cwd).

    Order: explicit Makefile (make) -> package.json "build" script
    (npm run build) -> pyproject.toml (python -m build). None when no build
    system is detectable — the caller then reports a clean error instead of
    guessing.
    """
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    if (base / "Makefile").exists():
        return "make"
    pkg = base / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
            if isinstance(scripts, dict) and scripts.get("build"):
                return "npm run build"
        except (ValueError, OSError):
            pass
    if (base / "pyproject.toml").exists():
        return f"{sys.executable} -m build"
    if (base / "setup.py").exists():
        return f"{sys.executable} setup.py build"
    return None


def run_build(
    command: Optional[str] = None,
    *,
    cwd: Optional[str | Path] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Run the project's build (auto-detected unless `command` is given)."""
    cmd = (command or "").strip() or detect_build_command(cwd)
    if not cmd:
        raise ValueError(
            "no build system detected (looked for Makefile, package.json "
            '"build" script, pyproject.toml, setup.py) — pass command explicitly')
    if cmd.endswith("-m build"):
        import importlib.util

        if importlib.util.find_spec("build") is None:
            return {
                "ok": False,
                "returncode": None,
                "timeout": False,
                "command": cmd,
                "output": "the 'build' package is not installed "
                          f"({sys.executable}). Install it with: "
                          f"{sys.executable} -m pip install build",
                "truncated": False,
            }
    res = _run(cmd, cwd=cwd, timeout=timeout, shell=True)
    out = str(res.get("output") or "")
    res["summary"] = out.strip().splitlines()[-5:] if out.strip() else []
    return res


EXEC_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a shell command in the task workspace. Use for mkdir, pytest, builds, scripts. Output is truncated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {"type": "integer", "description": "Timeout seconds 1-120 (default 30)", "minimum": 1, "maximum": 120},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": "Run Python code via `python -c`. Use for quick computation, transforms, or checks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source to execute"},
                    "timeout": {"type": "integer", "description": "Timeout seconds 1-120 (default 30)", "minimum": 1, "maximum": 120},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_test",
            "description": "Run pytest on a target path in the workspace. Returns pass/fail plus log tail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Pytest target (default '.')"},
                    "timeout": {"type": "integer", "description": "Timeout seconds 1-120 (default 60)", "minimum": 1, "maximum": 120},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_build",
            "description": "Run the project's build in the workspace (auto-detects Makefile, npm build script, or Python build; pass command to override). Returns pass/fail plus log tail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Explicit build command (default: auto-detect)"},
                    "timeout": {"type": "integer", "description": "Timeout seconds 1-120 (default 120)", "minimum": 1, "maximum": 120},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


def execute_exec_tool(
    name: str,
    arguments: str | Dict[str, Any],
    *,
    cwd: Optional[str | Path] = None,
) -> str:
    """Route an exec tool call. Returns JSON string (errors as {"error": ...})."""
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": f"invalid JSON arguments: {arguments[:200]}"})
    else:
        args = dict(arguments or {})
    effective_cwd = args.pop("_cwd", cwd)
    try:
        if name == "shell":
            res = shell(
                args.get("command", ""),
                cwd=effective_cwd,
                timeout=int(args.get("timeout") or _DEFAULT_TIMEOUT),
            )
            return json.dumps(res, ensure_ascii=False, default=str)
        if name == "python_exec":
            res = python_exec(
                args.get("code", ""),
                cwd=effective_cwd,
                timeout=int(args.get("timeout") or _DEFAULT_TIMEOUT),
            )
            return json.dumps(res, ensure_ascii=False, default=str)
        if name == "run_test":
            res = run_test(
                args.get("target", "."),
                cwd=effective_cwd,
                timeout=int(args.get("timeout") or 60),
            )
            return json.dumps(res, ensure_ascii=False, default=str)
        if name == "run_build":
            res = run_build(
                args.get("command") or None,
                cwd=effective_cwd,
                timeout=int(args.get("timeout") or 120),
            )
            return json.dumps(res, ensure_ascii=False, default=str)
        return json.dumps({"error": f"unknown exec tool {name!r}"})
    except Exception as exc:  # keep LLM loop alive
        return json.dumps({"error": str(exc)[:500]})


__all__ = [
    "EXEC_TOOLS",
    "shell",
    "python_exec",
    "run_test",
    "run_build",
    "detect_build_command",
    "destructive_command_reason",
    "execute_exec_tool",
]
