"""Report tool — write a deterministic report artifact inside a session root.

Contract: fn(args: dict) -> JSON string. Never raises.

  in:  {"action": "write|generate|save", "format": "md|json",
        "title": "Report", "sections": {"<heading>": value},
        "content": "<raw text, optional>",
        "path": "<file inside root, optional>",
        "overwrite": false, "root": "<sandbox dir, optional>"}
  out: '{"ok": true, "path", "relative", "bytes", "format"}' or
       '{"ok": false, "error": ...}'

Rendering is deterministic: md sections become headings/bullets/fenced JSON;
json is {"title", "sections"} sorted by key. Existing files are refused
unless overwrite is true. Sandbox: path resolves inside root via safe_join;
secrets/.git are denied. Writes are gated by the permission engine (ask).
"""

from __future__ import annotations

import json
import os
import re

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    from ..engine.sandbox import default_workspace, safe_join
except Exception:  # pragma: no cover — direct-path import fallback
    from nak_host.harness.engine.sandbox import default_workspace, safe_join  # type: ignore

from nak_host.utils.constant import (
    REPORT_DEFAULT_DIR as DEFAULT_DIR,
    REPORT_MAX_BYTES as MAX_BYTES,
)

_SLUG = re.compile(r"[^a-z0-9]+")


def _root(data: dict) -> str:
    return (str(data.get("root", "")).strip()
            or os.environ.get("NAK_WORKSPACE", "").strip()
            or str(default_workspace()))


def _slug(title: str) -> str:
    clean = _SLUG.sub("-", str(title or "").strip().lower()).strip("-")
    return (clean or "report")[:60]


def _inline(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _markdown(title: str, sections: Dict[str, Any]) -> str:
    lines = [f"# {title}", ""]
    for key, value in sections.items():
        lines.extend([f"## {key}", ""])
        if isinstance(value, (list, tuple)):
            lines.extend(f"- {_inline(item)}" for item in value)
        elif isinstance(value, dict):
            lines.extend(["```json",
                          json.dumps(value, indent=2, ensure_ascii=False,
                                     sort_keys=True, default=str),
                          "```"])
        else:
            lines.append(str(value))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report(args: dict) -> str:
    """Render and write one report artifact (md or json)."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower() or "write"
    if action not in ("write", "generate", "save"):
        return json.dumps({"ok": False,
                           "error": f"bad action {action!r}: want write|generate|save"})
    fmt = str(data.get("format", "md")).strip().lower() or "md"
    if fmt not in ("md", "json"):
        return json.dumps({"ok": False, "error": f"bad format {fmt!r}: want md|json"})
    title = str(data.get("title", "") or "Report").strip() or "Report"
    raw_content = data.get("content")
    if raw_content is not None:
        if not isinstance(raw_content, str):
            return json.dumps({"ok": False, "error": "content must be a string"})
        text = raw_content
        sections: Dict[str, Any] = {}
    else:
        sections = data.get("sections")
        if not isinstance(sections, dict):
            return json.dumps({"ok": False,
                               "error": "sections must be an object (or pass content)"})
        text = (_markdown(title, sections) if fmt == "md" else
                json.dumps({"title": title, "sections": sections}, indent=2,
                           ensure_ascii=False, sort_keys=True, default=str) + "\n")
    raw_path = str(data.get("path", "")).strip()
    if not raw_path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        raw_path = f"{DEFAULT_DIR}/{_slug(title)}-{stamp}.{fmt}"
    if not raw_path.lower().endswith(f".{fmt}"):
        return json.dumps({"ok": False,
                           "error": f"path must end with .{fmt}: {raw_path}"})
    ok, resolved = safe_join(_root(data), raw_path)
    if not ok:
        return json.dumps({"ok": False, "path": raw_path, "error": resolved})
    payload = text.encode("utf-8", "ignore")
    if len(payload) > MAX_BYTES:
        return json.dumps({"ok": False, "path": raw_path,
                           "error": f"report too large: {len(payload)} > {MAX_BYTES}"})
    p = Path(resolved)
    overwrite = data.get("overwrite") is True
    try:
        if p.exists() and not overwrite:
            return json.dumps({"ok": False, "path": str(p),
                               "error": f"already exists: {p} (pass overwrite: true)"})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return json.dumps({"ok": False, "path": str(p), "error": str(exc)[:300]})
    try:
        relative = os.path.relpath(str(p), _root(data))
    except (OSError, ValueError):
        relative = str(p)
    return json.dumps({"ok": True, "path": str(p), "relative": relative,
                       "bytes": len(payload), "format": fmt, "title": title})
