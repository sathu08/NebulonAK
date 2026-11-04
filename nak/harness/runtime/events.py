"""nak.harness.events -- structured event log (Phase 3).

Every state mutation already appends to HarnessState.observations (in-memory
debug trace). This module mirrors those events to
workspace/logs/events.jsonl so a finished/crashed session stays inspectable:

    {"ts": ..., "session": ..., "step": 2, "kind": "tool_result", "summary": ...}

Best-effort: logging never raises, never fails the turn.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nak.harness.events")


def log_event(
    state,
    kind: str,
    summary: str,
    *,
    classification: Optional[str] = None,
) -> None:
    """Record an observation in-state AND append to workspace event log."""
    try:
        state.record_observation(kind, summary)
    except Exception as exc:  # noqa: BLE001
        logger.debug("in-state observation failed: %s", exc)
        return
    try:
        ws = getattr(state, "workspace", None)
        if not ws:
            return
        logs_dir = Path(ws).parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "session": getattr(state, "session_id", None),
            "state_id": getattr(state, "state_id", None),
            "step": getattr(state, "current_step", 0),
            "kind": kind,
            "summary": (summary or "")[:1000],
        }
        if classification:
            entry["classification"] = classification
        with (logs_dir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("event log append failed: %s", exc)


__all__ = ["log_event"]
