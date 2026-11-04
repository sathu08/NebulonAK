"""Trace tool — read-only execution evidence from a file inside a session root.

Contract: fn(args: dict) -> JSON string. Never raises.

  in:  {"action": "trace|read|grep|tail", "path": "<file inside root>",
        "pattern": "<regex, optional>", "tail": N,
        "root": "<sandbox dir, optional>"}
  out: '{"ok": true, "path", "lines", "mode", "matches": [...]}' or
       '{"ok": false, "error": ...}'

Pattern mode returns matching lines with 1-based line numbers; tail mode
returns the last N lines. Missing pattern falls back to tail. Regex errors
are reported as ok:false. Sandbox: path resolves inside root via safe_join;
secrets/.git are denied. Read-only by design.
"""

from __future__ import annotations

import json
import os
import re

from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from ..engine.sandbox import default_workspace, safe_join
except Exception:  # pragma: no cover — direct-path import fallback
    from nak.harness.engine.sandbox import default_workspace, safe_join  # type: ignore

from nak.utils.constant import (
    TRACE_DEFAULT_TAIL as DEFAULT_TAIL,
    TRACE_MAX_CHARS as MAX_CHARS,
    TRACE_MAX_LINE_CHARS as MAX_LINE_CHARS,
    TRACE_MAX_LINES as MAX_LINES,
    TRACE_MAX_MATCHES as MAX_MATCHES,
)


def _root(data: dict) -> str:
    return (str(data.get("root", "")).strip()
            or os.environ.get("NAK_WORKSPACE", "").strip()
            or str(default_workspace()))


def _tail(data: dict) -> int:
    try:
        n = int(data.get("tail", DEFAULT_TAIL))
    except (TypeError, ValueError):
        n = DEFAULT_TAIL
    return max(1, min(n, MAX_LINES))


def _collect(entries: List[Tuple[int, str]]) -> Tuple[List[Dict[str, Any]], bool]:
    out: List[Dict[str, Any]] = []
    used = 0
    for lineno, line in entries:
        text = line[:MAX_LINE_CHARS]
        cost = len(text) + 16
        if used + cost > MAX_CHARS:
            return out, True
        out.append({"line": lineno, "text": text})
        used += cost
    return out, False


def trace(args: dict) -> str:
    """Return matching or trailing lines from one sandboxed file."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower() or "trace"
    if action not in ("trace", "read", "grep", "tail"):
        return json.dumps({"ok": False,
                           "error": f"bad action {action!r}: want trace|read|grep|tail"})
    raw = str(data.get("path", "")).strip()
    if not raw:
        return json.dumps({"ok": False, "error": "missing path: pass a file path"})
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "path": raw, "error": resolved})
    p = Path(resolved)
    if not p.exists():
        return json.dumps({"ok": False, "path": raw, "error": f"not found: {p}"})
    if not p.is_file():
        return json.dumps({"ok": False, "path": raw,
                           "error": f"not a file: {p} (trace is files-only)"})
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeError) as exc:
        return json.dumps({"ok": False, "path": raw, "error": str(exc)[:300]})
    pattern = str(data.get("pattern", data.get("grep", "")) or "").strip()
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return json.dumps({"ok": False, "path": raw,
                               "error": f"bad pattern: {exc}"[:300]})
        hits = [(i, ln) for i, ln in enumerate(lines, 1) if rx.search(ln)]
        matches, truncated = _collect(hits[:MAX_MATCHES])
        return json.dumps({"ok": True, "path": raw, "resolved": str(p),
                           "mode": "match", "pattern": pattern,
                           "lines": len(lines), "matched": len(hits),
                           "returned": len(matches),
                           "truncated": truncated or len(hits) > MAX_MATCHES,
                           "matches": matches})
    tail = _tail(data)
    start = max(0, len(lines) - tail)
    matches, truncated = _collect([(i, lines[i - 1]) for i in range(start + 1, len(lines) + 1)])
    return json.dumps({"ok": True, "path": raw, "resolved": str(p),
                       "mode": "tail", "tail": tail, "lines": len(lines),
                       "matched": None, "returned": len(matches),
                       "truncated": truncated, "matches": matches})
