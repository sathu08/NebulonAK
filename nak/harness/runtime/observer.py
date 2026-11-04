"""nak.harness.observer -- classify tool results (Phase 3).

The harness (not the LLM) decides what a result means. Every tool result is
classified into one of:

    SUCCESS | FAILURE | TIMEOUT | PARTIAL_SUCCESS | NEEDS_USER

Pure function, no network, fully offline-testable with scripted results:

    inspect('{"error": "tool ... denied (policy/user)"}') -> NEEDS_USER
    inspect('{"ok": false, "timeout": true, ...}')         -> TIMEOUT
    inspect(pytest_failed_output)                           -> FAILURE
"""

from __future__ import annotations

import json
from typing import Any, Dict

SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
TIMEOUT = "TIMEOUT"
PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
NEEDS_USER = "NEEDS_USER"

ALL = (SUCCESS, FAILURE, TIMEOUT, PARTIAL_SUCCESS, NEEDS_USER)


def _as_dict(result: str | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return {"output": str(result)}
    text = result.strip()
    if not text:
        return {"error": "empty result"}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {"output": text}
    except (ValueError, TypeError):
        return {"output": text}


def _output_text(data: Dict[str, Any]) -> str:
    parts = []
    for key in ("output", "content", "answer", "summary", "error", "logs"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
        elif isinstance(val, list):
            parts.append("\n".join(str(v) for v in val))
    return "\n".join(parts)


def inspect(result: str | Dict[str, Any]) -> Dict[str, str]:
    """Classify a tool result. Returns {status, reason}.

    Never raises — unparseable input becomes FAILURE with a reason.
    """
    try:
        data = _as_dict(result)
    except Exception as exc:  # noqa: BLE001
        return {"status": FAILURE, "reason": f"unparseable result: {exc}"}

    # 1. Explicit timeout signals (exec_tools timeout flag / expiry messages)
    if data.get("timeout") is True:
        return {"status": TIMEOUT, "reason": "tool call timed out"}
    err = str(data.get("error", "") or "")
    err_low = err.lower()
    if "timed out" in err_low or "timeout" in err_low:
        return {"status": TIMEOUT, "reason": err[:300] or "timeout"}

    # 2. Policy / user denials -> NEEDS_USER (denied flag or markers)
    if data.get("denied") is True:
        return {"status": NEEDS_USER, "reason": err[:300] or "tool use denied"}
    for marker in ("denied", "policy/user", "approval", "confirm"):
        if marker in err_low:
            return {"status": NEEDS_USER, "reason": err[:300]}

    # 3. Explicit errors -> FAILURE
    if "error" in data and str(data["error"]).strip():
        return {"status": FAILURE, "reason": str(data["error"])[:300]}

    # 4. Exec-style results (shell / python_exec / run_test)
    if "returncode" in data or "ok" in data:
        rc = data.get("returncode")
        ok = data.get("ok")
        out = _output_text(data)
        out_low = out.lower()
        # pytest verdict lines: "N failed" / "N passed"
        has_failed = "failed" in out_low and "0 failed" not in out_low
        has_passed = "passed" in out_low and "0 passed" not in out_low
        if has_failed and has_passed:
            return {"status": PARTIAL_SUCCESS, "reason": "some checks passed, some failed"}
        if has_failed:
            return {"status": FAILURE, "reason": "tests/checks failed"}
        if rc is not None and rc not in (0, 5):  # 5 = pytest "no tests collected"
            return {"status": FAILURE, "reason": f"command exited {rc}"}
        if ok is False:
            return {"status": FAILURE, "reason": out[:300] or "tool reported failure"}
        # explicit partial flag from future tools
        if data.get("partial") is True:
            return {"status": PARTIAL_SUCCESS, "reason": out[:300] or "partial result"}
        return {"status": SUCCESS, "reason": out[-300:] or "ok"}

    # 5. File / memory tools: no error key + payload -> SUCCESS
    # (truncation flags are informational, not partial — the read succeeded)
    return {"status": SUCCESS, "reason": "ok"}


__all__ = [
    "inspect",
    "SUCCESS",
    "FAILURE",
    "TIMEOUT",
    "PARTIAL_SUCCESS",
    "NEEDS_USER",
    "ALL",
]
