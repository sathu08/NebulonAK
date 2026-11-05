"""Approval ledger: mint, validate and revoke approval references.

The agent loop mints an ApprovalRef when a human approver says yes, embeds
it in a derived ExecutionContext, and the harness executor re-validates it
at execution time. Revocation is honored mid-run; expiry is enforced.
"""

from __future__ import annotations

import threading
import uuid

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from nak_host.domain.permission import ApprovalRef


def mint_approval(scope: str = "task", granted_by: str = "approver",
                  expires_at: Any = None) -> ApprovalRef:
    """Create a fresh approval reference (not yet granted — ledger grants)."""
    return ApprovalRef(id=f"appr-{uuid.uuid4().hex[:12]}", scope=scope,
                       granted_by=str(granted_by or "approver"),
                       granted_at=datetime.now(timezone.utc),
                       expires_at=expires_at, revoked=False)


class ApprovalLedger:
    """In-memory grant store backing ApprovalAuthority (thread-safe)."""

    def __init__(self, clock: Any = None) -> None:
        import time as _time

        self._clock = clock or _time.time
        self._lock = threading.Lock()
        self._granted: dict = {}
        self._revoked: set = set()

    def grant(self, ref: ApprovalRef) -> ApprovalRef:
        """Record a grant (returns the ref unchanged, for chaining)."""
        with self._lock:
            self._granted[ref.id] = ref
            self._revoked.discard(ref.id)
        return ref

    def revoke(self, ref_id: str) -> None:
        """Revoke a grant (future validations fail)."""
        with self._lock:
            self._revoked.add(str(ref_id or ""))

    def is_valid(self, ref: ApprovalRef | None, ctx: Any = None) -> bool:
        """True when the ref is granted, unrevoked, unexpired and in scope."""
        if ref is None or not isinstance(ref, ApprovalRef):
            return False
        if bool(ref.revoked):
            return False
        with self._lock:
            if ref.id in self._revoked:
                return False
            granted = self._granted.get(ref.id)
        if granted is None:
            return False
        if ref.expires_at is not None:
            try:
                expired = ref.expires_at.timestamp() <= float(self._clock())
            except Exception:
                return False
            if expired:
                return False
        if ctx is not None:
            scope = str(ref.scope or "").strip().lower()
            if scope not in ("task", "step", "tool"):
                return False
        return True

    def derive(self, ctx: Any, ref: ApprovalRef) -> Any:
        """Copy an ExecutionContext carrying this approval (frozen-safe)."""
        return replace(ctx, approval=ref)
