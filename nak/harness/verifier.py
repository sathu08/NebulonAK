"""nak.harness.verifier -- independent completion checks (Phase 4).

Key principle: the harness — not the LLM's `{"answer": ...}` — decides
whether the task is actually done. After an agent claims completion, the
verifier runs real checks in the task workspace:

    default checks = [compileall, pytest]

Returns {ok, failures, logs, checks:[{name, command, ok, returncode,
output_tail}]}. Full logs land in workspace/logs/verify_<ts>.log and the
summary is appended to state.verification_results[] by HarnessRuntime.

Config (env wins, then nebulonak.cfg [harness], else defaults):
    verify_commands  JSON list or ';'-separated commands (overrides defaults)
    verify_timeout   per-check seconds (default 60, max 300)

Conventions:
    - pytest exit 5 ("no tests collected") counts as OK — an empty workspace
      is not a failure.
    - Never raises: misconfiguration becomes a failed check entry.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nak.harness.verifier")

_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 300
_MAX_TAIL = 4000


def default_checks(project: str | Path) -> List[Dict[str, str]]:
    """Baseline checks for a Python workspace (run with cwd=workspace)."""
    py = sys.executable
    return [
        {"name": "compileall", "command": f"{py} -m compileall -q ."},
        {"name": "pytest", "command": f"{py} -m pytest -q ."},
    ]


def configured_checks(project: str | Path) -> List[Dict[str, str]]:
    """Checks from config (env NAK_VERIFY_COMMANDS, else cfg [harness]), else defaults."""
    from nak.utils.config import get_verify_commands

    raw = get_verify_commands().strip()
    if not raw:
        return default_checks(project)
    try:
        if raw.startswith("["):
            items = json.loads(raw)
            out = []
            for i, item in enumerate(items):
                if isinstance(item, str):
                    out.append({"name": f"check_{i}", "command": item})
                elif isinstance(item, dict) and item.get("command"):
                    out.append({"name": str(item.get("name") or f"check_{i}"),
                                "command": str(item["command"])})
            if out:
                return out
    except (ValueError, TypeError):
        pass
    return [{"name": f"check_{i}", "command": cmd.strip()}
            for i, cmd in enumerate(raw.split(";")) if cmd.strip()]


def verify_timeout() -> int:
    """Per-check seconds via config (default 60, clamped 1..300)."""
    from nak.utils.config import get_verify_timeout

    return get_verify_timeout()


class Verifier:
    """Run independent checks against a task workspace."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        timeout: Optional[int] = None,
        checks: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.timeout = timeout or verify_timeout()
        self.checks = checks if checks is not None else configured_checks(self.workspace)

    def _run_one(self, name: str, command: str) -> Dict[str, Any]:
        started = time.time()
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, errors="replace", timeout=self.timeout,
                cwd=str(self.workspace),  # relative commands resolve in-task
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            rc = proc.returncode
            # pytest exit 5 = no tests collected -> not a failure
            ok = rc == 0 or (name == "pytest" and rc == 5)
            if name == "pytest" and "No module named pytest" in output:
                output += ("\nHINT: pytest is not installed for the check interpreter "
                           f"({sys.executable}). Install it with: "
                           f"{sys.executable} -m pip install pytest "
                           "(it is a runtime dependency in pyproject.toml).")
            return {"name": name, "command": command, "ok": ok,
                    "returncode": rc, "timeout": False,
                    "output_tail": output[-_MAX_TAIL:],
                    "elapsed": round(time.time() - started, 2)}
        except subprocess.TimeoutExpired as exc:
            out = str((exc.stdout or "") + (exc.stderr or ""))
            return {"name": name, "command": command, "ok": False,
                    "returncode": None, "timeout": True,
                    "output_tail": out[-_MAX_TAIL:] or f"timed out after {self.timeout}s",
                    "elapsed": round(time.time() - started, 2)}
        except Exception as exc:  # noqa: BLE001
            return {"name": name, "command": command, "ok": False,
                    "returncode": None, "timeout": False,
                    "output_tail": str(exc)[:1000],
                    "elapsed": round(time.time() - started, 2)}

    def verify(self) -> Dict[str, Any]:
        """Run all checks. Never raises — failures are data."""
        results = [self._run_one(c["name"], c["command"]) for c in self.checks]
        failures = [r["name"] for r in results if not r["ok"]]
        summary: Dict[str, Any] = {
            "ok": not failures,
            "failures": failures,
            "checks": results,
            "workspace": str(self.workspace),
        }
        self._write_log(summary)
        return summary

    def _write_log(self, summary: Dict[str, Any]) -> None:
        """Best-effort full log to workspace/logs/ (sibling of project/)."""
        try:
            base = self.workspace.parent if self.workspace.name == "project" else self.workspace
            logs_dir = base / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            fname = f"verify_{int(time.time())}.log"
            lines = [f"# verify {time.ctime()} workspace={self.workspace}",
                     f"# ok={summary['ok']} failures={summary['failures']}"]
            for r in summary["checks"]:
                lines.append(f"\n## {r['name']}: {'OK' if r['ok'] else 'FAIL'} "
                             f"(rc={r['returncode']} {r['elapsed']}s)")
                lines.append(f"$ {r['command']}")
                lines.append(r["output_tail"])
            (logs_dir / fname).write_text("\n".join(lines), encoding="utf-8")
            summary["log"] = str(logs_dir / fname)
        except Exception as exc:  # noqa: BLE001
            logger.debug("verify log write failed: %s", exc)


__all__ = ["Verifier", "default_checks", "configured_checks", "verify_timeout"]
