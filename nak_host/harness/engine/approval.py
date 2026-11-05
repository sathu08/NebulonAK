"""Approval (ask-verdict) handling — canonical spelling.

Replaces the misspelled `approvel.py` (removed; it remains as a thin
deprecated shim only if something still imports it — do not use it).

One rule: an 'ask' verdict never auto-runs. Approver returns True
(fn(tool, args, worker_label, what)) -> proceed (None). No approver,
approver error, or decline -> deny + need_approval flag for the
harness/pipeline to resolve.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .permission import deny


def ask(tool: str, given: Dict[str, Any], worker_label: str,
        what: str, approver: Optional[Callable[..., bool]] = None,
        fallback_approver: Optional[Callable[..., bool]] = None) -> Optional[Dict[str, Any]]:
    """Resolve an 'ask' verdict. Approver True -> None (proceed).

    No approver (or decline) -> deny + need_approval flag for the
    harness/pipeline to resolve (never auto-runs).
    """
    fn = approver if approver is not None else fallback_approver
    approved = False
    if callable(fn):
        try:
            approved = bool(fn(tool, dict(given or {}), worker_label, what))
        except Exception:
            approved = False
    if approved:
        return None
    return deny(tool, worker_label,
                f"{what} needs approval for worker {worker_label!r}",
                need_approval=True)
