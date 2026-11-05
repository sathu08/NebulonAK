"""Observe tool — file facts for decide(), sandboxed to a session root.

Contract: fn(args: dict) -> JSON string. Every call takes:
  in:  {"action": "capture|read", "path": "<file inside root, required>",
        "root": "<sandbox dir, optional>"}
  out: '{"ok": true, ...}' or '{"ok": false, "error": ...}'

Scope is FILES ONLY by design (no directories — use the directory tool
for listing). One call packs what decide() wants without a second trip:
content + size + session in a single JSON bundle.

  out ok:   {"ok": true, "type": "file", "session": "<id>",
             "path": "<as given>", "resolved": "<abs>",
             "content": "<text>", "content_chars": N, "truncated": bool}
  out fail: {"ok": false, "type": "file", "session": "<id>",
             "path": "<as given>", "error": ...}

Sandbox: path must resolve inside root via safe_join (no .. / absolute /
symlink escapes); .env, *.key/*.pem and secrets/ + .git/ are denied even
inside root. Root defaults to NAK_WORKSPACE env or repo workspace (the
engine passes the session dir as root at run). Permission (allow/ask/deny)
is enforced by the engine inspect() BEFORE any run. No Brain/memory access
here by design — grounding stays worker-side (GenericWorker recall).
Always returns a string, never raises — engine-safe.
"""

from __future__ import annotations

import json
import os

from pathlib import Path

try:
    from ..engine.sandbox import default_workspace, safe_join
except Exception:  # pragma: no cover — direct-path import fallback
    from nak.harness.engine.sandbox import default_workspace, safe_join  # type: ignore

from nak.utils.constant import OBSERVE_MAX_CHARS as MAX_CHARS


def _root(data: dict) -> str:
    return (str(data.get("root", "")).strip()
            or os.environ.get("NAK_WORKSPACE", "").strip()
            or str(default_workspace()))


def _session(data: dict) -> str:
    """Best-effort session id: last two path parts of the root."""
    try:
        parts = [p for p in str(_root(data)).split(os.sep) if p.strip()]
        return os.sep.join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "")
    except Exception:
        return ""


def capture_file(args: dict) -> str:
    """Capture one file inside the sandbox root (text mode, capped)."""
    data = args or {}
    raw = str(data.get("path", "")).strip()
    if not raw:
        return json.dumps({"ok": False, "type": "file",
                           "session": _session(data), "path": raw,
                           "error": "missing path: worker must pass a file path"})
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "type": "file",
                           "session": _session(data), "path": raw,
                           "error": resolved})
    p = Path(resolved)
    if not p.exists():
        return json.dumps({"ok": False, "type": "file",
                           "session": _session(data), "path": raw,
                           "error": f"not found: {p}"})
    if not p.is_file():
        return json.dumps({"ok": False, "type": "file",
                           "session": _session(data), "path": raw,
                           "error": f"not a file: {p} (observe is files-only)"})
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError) as exc:
        return json.dumps({"ok": False, "type": "file",
                           "session": _session(data), "path": raw,
                           "error": str(exc)[:300]})
    truncated = False
    if len(text) > MAX_CHARS:
        text, truncated = text[:MAX_CHARS] + "\n…(truncated)", True
    return json.dumps({"ok": True, "type": "file",
                       "session": _session(data), "path": raw,
                       "resolved": str(p), "content": text,
                       "content_chars": len(text), "truncated": truncated})


def observe(args: dict) -> str:
    """Single observe tool: route args.action (capture|read) to capture_file."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower() or "capture"
    if action in ("capture", "read", "cat", "get"):
        return capture_file(data)
    return json.dumps({"ok": False, "type": "file",
                       "session": _session(data),
                       "path": str(data.get("path", "")),
                       "error": f"bad action {str(data.get('action', ''))!r}: want capture|read"})
