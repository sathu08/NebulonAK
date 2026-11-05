"""Capability — moved out of engine.py / permission.py.

Maps tool-spec capabilities (tools.json) to dotted permission
capabilities (permissions.json). Pure helpers only.
"""

from __future__ import annotations

from typing import Any, Dict, List

from nak_host.utils.constant import CAP_MAP


def required_caps(spec: Dict[str, Any]) -> List[str]:
    """Dotted capabilities a tool needs: explicit permission_caps or mapped."""
    declared = spec.get("permission_caps")
    if isinstance(declared, list) and declared:
        return [str(c).strip() for c in declared if str(c).strip()]
    out = []
    for c in (spec.get("capabilities") or []):
        c = str(c).strip()
        if c:
            out.append(CAP_MAP.get(c, f"cap.{c}"))
    return out
