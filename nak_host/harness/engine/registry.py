"""Registry — moved out of engine.py.

Tool catalog (nak/manifests/tools.json): name / module / function /
capabilities / args. Validated on load; functions resolve lazily
in execution.py via importlib only at run time.
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import Dict, List

from nak.utils.constant import DEFAULT_TOOLS_JSON as DEFAULT_REGISTRY


def load_registry(registry: str | Path | None = None) -> List[Dict]:
    path = Path(registry).expanduser() if registry else DEFAULT_REGISTRY
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"tool registry unreadable {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"tool registry {path} must be a JSON list")
    for i, e in enumerate(raw):
        if not isinstance(e, dict) or not str(e.get("name", "")).strip():
            raise ValueError(f"tool registry entry {i}: need a non-empty 'name'")
        for key in ("module", "function"):
            if not str(e.get(key, "")).strip():
                raise ValueError(f"tool {e.get('name')!r}: missing {key!r} location")
    return raw
