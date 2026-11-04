"""Approval ledger tests (offline)."""

from __future__ import annotations

from datetime import datetime, timezone

from nak.application import ApprovalLedger, mint_approval


def test_grant_validate_revoke():
    ledger = ApprovalLedger()
    ref = mint_approval()
    assert ledger.is_valid(ref) is False
    ledger.grant(ref)
    assert ledger.is_valid(ref) is True
    ledger.revoke(ref.id)
    assert ledger.is_valid(ref) is False


def test_unknown_and_bad_scope_rejected():
    ledger = ApprovalLedger()
    assert ledger.is_valid(None) is False
    assert ledger.is_valid("not-a-ref") is False
    ref = mint_approval()
    object.__setattr__(ref, "scope", "bogus")
    ledger.grant(ref)
    assert ledger.is_valid(ref, ctx=object()) is False


def test_expiry_and_derive():
    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    clock = Clock()
    ledger = ApprovalLedger(clock=clock)
    ref = mint_approval(expires_at=datetime.fromtimestamp(1005, tz=timezone.utc))
    ledger.grant(ref)
    assert ledger.is_valid(ref) is True
    clock.t = 1006.0
    assert ledger.is_valid(ref) is False

    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Ctx:
        approval: object = None

    derived = ledger.derive(Ctx(), ref)
    assert derived.approval is ref
