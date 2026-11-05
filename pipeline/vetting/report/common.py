"""Shared formatting and writing helpers for vetting reports."""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from nak_host.harness.tools.report import report


def clip(text: Any, limit: int = 2000) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "\n…(truncated)"


def format_score(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def probe_rows(probes: list[Any]) -> list[str]:
    if not probes:
        return ["- (none)"]
    rows = []
    for p in probes:
        detail = f" — {p.detail}" if p.detail else ""
        rows.append(f"- `{p.name}`: **{p.status}**{detail}")
    return rows


def write(root: Path, path: str, fmt: str, content: str) -> str:
    res = json.loads(report({
        "action": "write", "format": fmt, "content": content,
        "path": path, "root": str(root),
    }))
    if not res.get("ok"):
        raise RuntimeError(f"report write failed for {path}: {res.get('error')}")
    return str(res.get("path", path))
