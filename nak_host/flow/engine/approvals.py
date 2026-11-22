"""Pending approvals — lets a running pipeline WAIT for user click.

Flow (Auto-approve OFF):
  runner blocks in approver() -> create() -> wait() (up to 120s)
  UI polls GET /approvals while run request is open
  user clicks Approve/Deny -> POST /approvals/{id} -> runner unblocks

In-memory only (restart clears). Thread-safe via Lock.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any


_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}
_TIMEOUT = 120.0


def create(tool: str, args: dict[str, Any], worker: str, what: str) -> str:
    """Register a pending approval, return id."""
    aid = "appr-" + secrets.token_hex(4)
    with _LOCK:
        _PENDING[aid] = {
            "id": aid,
            "tool": tool,
            "args": args,
            "worker": worker,
            "what": what,
            "decision": None,
            "created": time.time(),
        }
    return aid


def decide(aid: str, approved: bool) -> bool:
    """User decision. Returns False when unknown/already decided."""
    with _LOCK:
        entry = _PENDING.get(aid)
        if entry is None or entry.get("decision") is not None:
            return False
        entry["decision"] = bool(approved)
        return True


def wait(aid: str, timeout: float = _TIMEOUT) -> bool:
    """Block until user decides or timeout (False = deny/timeout)."""
    end = time.time() + max(1.0, min(300.0, float(timeout or _TIMEOUT)))
    while time.time() < end:
        with _LOCK:
            entry = _PENDING.get(aid)
            if entry is None:
                return False
            if entry.get("decision") is True:
                _PENDING.pop(aid, None)
                return True
            if entry.get("decision") is False:
                _PENDING.pop(aid, None)
                return False
        time.sleep(1.0)
    with _LOCK:
        _PENDING.pop(aid, None)
    return False


def list_pending() -> list[dict[str, Any]]:
    """Pending (undecided, unexpired) approvals for UI polling."""
    now = time.time()
    out: list[dict[str, Any]] = []
    with _LOCK:
        for aid, e in list(_PENDING.items()):
            if e.get("decision") is not None:
                continue
            if now - float(e.get("created", now)) > _TIMEOUT + 5:
                continue
            out.append(
                {
                    "id": aid,
                    "tool": e.get("tool"),
                    "worker": e.get("worker"),
                    "what": e.get("what"),
                    "args": e.get("args"),
                }
            )
    return out
