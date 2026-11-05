"""Assertion tool — deterministic expectation checks inside a session root.

Contract: fn(args: dict) -> JSON string. Never raises.

  in:  {"action": "check", "checks": [{"id", "op", "expected", "actual"}],
        "suite_path": "<json file inside root, optional>",
        "root": "<sandbox dir, optional>"}
  out: '{"ok": true, "all_passed": bool, "results": [...]}' or
       '{"ok": false, "error": ...}'

Ops: eq | ne | contains | regex | gte | lte | in. Nothing is evaluated as
code (no eval/exec); a bad op or regex fails that check, not the call.
Sandbox: suite_path resolves inside root via safe_join; secrets/.git are
denied. Permission verdicts are enforced by the engine before any run.
"""

from __future__ import annotations

import json
import os
import re

from pathlib import Path
from typing import Any, Dict, List

try:
    from ..engine.sandbox import default_workspace, safe_join
except Exception:  # pragma: no cover — direct-path import fallback
    from nak.harness.engine.sandbox import default_workspace, safe_join  # type: ignore

from nak.utils.constant import (
    ASSERT_MAX_CHECKS as MAX_CHECKS,
    ASSERT_MAX_VALUE_CHARS as MAX_VALUE_CHARS,
)

_OPS = ("eq", "ne", "contains", "regex", "gte", "lte", "in")


def _root(data: dict) -> str:
    return (str(data.get("root", "")).strip()
            or os.environ.get("NAK_WORKSPACE", "").strip()
            or str(default_workspace()))


def _value(value: Any) -> Any:
    """Keep small values structured; clip large ones to a display string."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) <= MAX_VALUE_CHARS:
        return value
    return text[:MAX_VALUE_CHARS] + "…"


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_one(item: Any, index: int) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {"id": f"check-{index}", "op": "", "passed": False,
                "error": "check must be an object"}
    cid = str(item.get("id", "") or f"check-{index}")
    op = str(item.get("op", "")).strip().lower()
    has_expected = "expected" in item
    has_actual = "actual" in item
    expected = item.get("expected")
    actual = item.get("actual")
    out: Dict[str, Any] = {"id": cid, "op": op,
                           "expected": _value(expected),
                           "actual": _value(actual)}
    if op not in _OPS:
        out.update(passed=False, error=f"bad op {op!r}: want {'|'.join(_OPS)}")
        return out
    if not has_expected or not has_actual:
        out.update(passed=False, error="check needs both expected and actual")
        return out
    try:
        if op == "eq":
            passed = expected == actual
        elif op == "ne":
            passed = expected != actual
        elif op == "contains":
            passed = (expected in actual if isinstance(actual, (list, tuple, dict))
                      else str(expected) in str(actual))
        elif op == "in":
            passed = actual in expected if isinstance(expected, (list, tuple, dict, str)) else False
        elif op == "regex":
            passed = re.search(str(expected), str(actual)) is not None
        else:
            if not (_number(expected) and _number(actual)):
                raise ValueError("gte/lte need numeric expected and actual")
            passed = actual >= expected if op == "gte" else actual <= expected
    except (re.error, TypeError, ValueError) as exc:
        out.update(passed=False, error=str(exc)[:300])
        return out
    out["passed"] = bool(passed)
    return out


def _load_suite(data: dict) -> List[Any]:
    raw = str(data.get("suite_path", "")).strip()
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        raise ValueError(resolved)
    text = Path(resolved).read_text(encoding="utf-8", errors="replace")
    suite = json.loads(text)
    if not isinstance(suite, list):
        raise ValueError(f"suite must be a JSON list: {resolved}")
    return suite


def assertion(args: dict) -> str:
    """Run one assertion suite (inline checks or a JSON suite file)."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower() or "check"
    if action not in ("check", "run", "assert"):
        return json.dumps({"ok": False,
                           "error": f"bad action {action!r}: want check|run|assert"})
    checks = data.get("checks")
    suite_path = str(data.get("suite_path", "")).strip()
    if checks is not None and suite_path:
        return json.dumps({"ok": False, "error": "pass either checks or suite_path, not both"})
    if checks is None:
        if not suite_path:
            return json.dumps({"ok": False, "error": "missing checks: pass checks or suite_path"})
        try:
            checks = _load_suite(data)
        except (OSError, UnicodeError, ValueError) as exc:
            return json.dumps({"ok": False, "error": str(exc)[:300]})
    if not isinstance(checks, list) or not checks:
        return json.dumps({"ok": False, "error": "checks must be a non-empty list"})
    if len(checks) > MAX_CHECKS:
        return json.dumps({"ok": False,
                           "error": f"too many checks: {len(checks)} > {MAX_CHECKS}"})
    results = [_check_one(item, i) for i, item in enumerate(checks, 1)]
    passed = sum(1 for r in results if r.get("passed"))
    return json.dumps({"ok": True, "all_passed": passed == len(results),
                       "total": len(results), "passed": passed,
                       "failed": len(results) - passed, "results": results})
