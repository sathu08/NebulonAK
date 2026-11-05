"""Search tool — glob + regex grep inside the sandbox root (read-only).

Contract: fn(args: dict) -> JSON string. Every call takes:
  in:  {"action": "glob|grep", "pattern": "<glob or regex>",
        "path": "<dir or file inside root, optional>",
        "include": "<glob filter for grep, optional>",
        "max_results": "<int, optional>", "root": "<sandbox dir, optional>"}
  out: '{"ok": true, ...}' or '{"ok": false, "error": ...}'

Read-only, sandboxed: every candidate resolves through safe_join, so `..`,
absolute paths, symlink escapes and denied names (.env, keys, secrets/, .git)
cannot leak. Heavy build/cache dirs are skipped. Always returns a string.
"""

from __future__ import annotations

import json
import re

from pathlib import Path
from typing import Any
from collections.abc import Iterator

try:
    from ..engine.sandbox import default_workspace, safe_join
except Exception:  # pragma: no cover — direct-path import fallback
    from nak_host.harness.engine.sandbox import default_workspace, safe_join  # type: ignore

from nak_host.utils.constant import SEARCH_DEFAULT_MAX_RESULTS as _DEFAULT_MAX_RESULTS
from nak_host.utils.constant import SEARCH_MAX_FILE_BYTES as _MAX_FILE_BYTES
from nak_host.utils.constant import SEARCH_MAX_LINE_CHARS as _MAX_LINE_CHARS
from nak_host.utils.constant import SEARCH_MAX_RESULTS_CAP as _MAX_RESULTS_CAP
from nak_host.utils.constant import SEARCH_SKIP_DIRS as _SKIP_DIRS


def _root(data: dict) -> str:
    return (str(data.get("root", "")).strip()
            or str(default_workspace()))


def _max_results(data: dict) -> int:
    try:
        value = int(data.get("max_results", _DEFAULT_MAX_RESULTS))
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_RESULTS
    return max(1, min(_MAX_RESULTS_CAP, value))


def _skipped(path: Path, base: Path) -> bool:
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return True
    return any(part in _SKIP_DIRS for part in parts)


def _iter_files(base: Path, include: str) -> Iterator[Path]:
    pattern = include.strip() or "*"
    try:
        candidates = base.rglob(pattern)
    except (OSError, ValueError):
        return
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            if _skipped(candidate, base):
                continue
        except OSError:
            continue
        yield candidate


def _glob(data: dict) -> str:
    pattern = str(data.get("pattern", "")).strip()
    if not pattern:
        return json.dumps({"ok": False, "error": "missing pattern for glob"})
    raw = str(data.get("path", "")).strip() or "."
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    base = Path(resolved)
    if not base.is_dir():
        return json.dumps({"ok": False, "error": f"not a directory: {base}"})
    limit = _max_results(data)
    matches: list[dict[str, Any]] = []
    truncated = False
    for path in _iter_files(base, pattern):
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            continue
        matches.append({"path": rel, "size": path.stat().st_size})
        if len(matches) >= limit:
            truncated = True
            break
    return json.dumps({"ok": True, "action": "glob", "pattern": pattern,
                       "base": str(base), "matches": matches,
                       "count": len(matches), "truncated": truncated})


def _grep(data: dict) -> str:
    pattern = str(data.get("pattern", "")).strip()
    if not pattern:
        return json.dumps({"ok": False, "error": "missing pattern for grep"})
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return json.dumps({"ok": False, "error": f"bad regex: {exc}"})
    raw = str(data.get("path", "")).strip() or "."
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    base = Path(resolved)
    if not base.exists():
        return json.dumps({"ok": False, "error": f"not found: {base}"})
    include = str(data.get("include", "")).strip()
    limit = _max_results(data)
    matches: list[dict[str, Any]] = []
    truncated = False
    files = [base] if base.is_file() else list(
        _iter_files(base, include))
    for path in files:
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.name if base.is_file() else path.relative_to(base).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            if not regex.search(line):
                continue
            matches.append({"path": rel, "line": number,
                            "text": line.strip()[:_MAX_LINE_CHARS]})
            if len(matches) >= limit:
                truncated = True
                break
        if truncated:
            break
    return json.dumps({"ok": True, "action": "grep", "pattern": pattern,
                       "base": str(base), "matches": matches,
                       "count": len(matches), "truncated": truncated})


def search(args: dict) -> str:
    """Route args.action (glob|grep) to the read-only search above."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower() or "grep"
    if action in ("glob", "find", "files"):
        return _glob(data)
    if action in ("grep", "search", "content"):
        return _grep(data)
    return json.dumps({"ok": False,
                       "error": f"bad action {action!r}: want glob|grep"})
