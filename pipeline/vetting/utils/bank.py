"""Reviewed case banks: frozen, human-approved questions per worker.

A bank file is ``cases/<worker>.v<version>.json`` with ``{worker, version,
cases}``. Graded runs load banks instead of asking Nunnan to invent fresh
questions every run, so scores stay comparable across runs, workers and
code changes. Generative runs remain available for discovery.
"""

from __future__ import annotations

import json
import re

from pathlib import Path
from typing import Any

from .models import Case
from .prompting import _check_case

BANK_DIR = Path(__file__).resolve().parent.parent / "cases"
_BANK_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)\.v(\d+)\.json$")


def list_banks(bank_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Reviewed banks on disk: {worker, version, path}, newest first."""
    root = Path(bank_dir).expanduser() if bank_dir else BANK_DIR
    found: list[dict[str, Any]] = []
    try:
        files = sorted(root.glob("*.json"))
    except OSError:
        return []
    for path in files:
        match = _BANK_RE.match(path.name)
        if match:
            found.append({"worker": match.group(1),
                          "version": int(match.group(2)),
                          "path": str(path)})
    found.sort(key=lambda e: (e["worker"], -e["version"]))
    return found


def load_bank(spec: str | Path, worker: str | None = None) -> dict[str, Any]:
    """Load one bank: {worker, version, path, cases}.

    ``spec`` is a file path, a worker name (latest version), or
    ``worker.vN``. Shape errors raise ValueError with file context.
    """
    path: Path | None = None
    text = str(spec or "").strip()
    if text:
        candidate = Path(text).expanduser()
        if candidate.is_file():
            path = candidate
    if path is None and text:
        name, _, version = text.partition(".v")
        name = name.strip()
        if not name:
            raise ValueError(f"bad bank spec {text!r} (want <worker>[.vN] or a path)")
        cands = [e for e in list_banks()
                 if e["worker"] == name
                 and (not version.strip() or e["version"] == _version(version, text))]
        if not cands:
            raise ValueError(f"no bank for {text!r}")
        path = Path(cands[0]["path"])
    if path is None:
        raise ValueError(f"bad bank spec {text!r} (want <worker>[.vN] or a path)")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"bank file unreadable {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"bank {path} must be a JSON object")
    bank_worker = str(raw.get("worker", "") or "").strip()
    if not bank_worker:
        raise ValueError(f"bank {path} needs a non-empty 'worker'")
    try:
        bank_version = int(raw.get("version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bank {path} needs an integer 'version'") from exc
    items = raw.get("cases")
    if not isinstance(items, list) or not items:
        raise ValueError(f"bank {path} needs a non-empty 'cases' list")
    cases = [_check_case(item, i) for i, item in enumerate(items, 1)]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"bank {path} case ids must be unique: {ids}")
    return {"worker": bank_worker, "version": bank_version,
            "path": str(path), "cases": cases}


def _version(text: str, spec: str) -> int:
    try:
        return int(text.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bad bank spec {spec!r} (want <worker>[.vN])") from exc


def resolve_bank(cases_from: Any, worker_name: str) -> list[Case] | None:
    """Bank cases for one worker, or None when grading generatively.

    A path bank must declare this worker; a ``worker[@vN]`` spec must name
    this worker. Anything else raises ValueError (fail fast, never silently
    vet the wrong worker on the wrong questions).
    """
    if cases_from is None:
        return None
    if isinstance(cases_from, list):
        return list(cases_from)
    text = str(cases_from or "").strip()
    if not text:
        return None
    bank = load_bank(text)
    if bank["worker"] != worker_name:
        raise ValueError(f"bank {bank['path']} is for worker "
                         f"{bank['worker']!r}, not {worker_name!r}")
    return list(bank["cases"])
