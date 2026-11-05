"""Limits — moved out of engine.py / permission.py.

Session limits: calls, cumulative time, file/content size.
Pure helpers only; counters live on the harness, passed in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from nak_host.utils.constant import DEFAULT_LIMITS


def norm_limits(value: Any) -> Dict[str, float]:
    out = dict(DEFAULT_LIMITS)
    if isinstance(value, dict):
        for key in out:
            try:
                out[key] = float(value.get(key, out[key]))
            except (TypeError, ValueError):
                pass
    return out


def check_limits(given: Dict[str, Any], resolved: Optional[str],
                 entry: Dict[str, Any], calls: int,
                 exec_seconds: float) -> Optional[str]:
    """Session limits: calls, cumulative time, file/content size."""
    lim = entry.get("limits") or {}
    try:
        if calls >= int(lim.get("max_tool_calls", DEFAULT_LIMITS["max_tool_calls"])):
            return f"limit: max_tool_calls ({lim.get('max_tool_calls', DEFAULT_LIMITS['max_tool_calls'])}) reached"
    except (TypeError, ValueError):
        pass
    try:
        if exec_seconds >= float(lim.get("max_execution_time", DEFAULT_LIMITS["max_execution_time"])):
            return f"limit: max_execution_time ({lim.get('max_execution_time', DEFAULT_LIMITS['max_execution_time'])}s) exceeded"
    except (TypeError, ValueError):
        pass
    try:
        cap = int(lim.get("max_file_size", DEFAULT_LIMITS["max_file_size"]))
    except (TypeError, ValueError):
        cap = int(DEFAULT_LIMITS["max_file_size"])
    if resolved:
        try:
            if Path(resolved).is_file() and Path(resolved).stat().st_size > cap:
                return f"limit: file over max_file_size ({cap} bytes)"
        except OSError:
            pass
    for key in ("content", "text", "data", "body"):
        val = (given or {}).get(key)
        if isinstance(val, (bytes, bytearray)) and len(val) > cap:
            return f"limit: {key} over max_file_size ({cap} bytes)"
        if isinstance(val, str) and len(val.encode("utf-8", "ignore")) > cap:
            return f"limit: {key} over max_file_size ({cap} bytes)"
    return None
