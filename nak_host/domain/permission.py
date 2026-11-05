"""Permission and approval domain values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType

from .errors import ValidationError
from .freeze import deep_freeze

APPROVAL_SCOPES: frozenset[str] = frozenset({"task", "step", "tool"})
BLOCK_CODES: frozenset[str] = frozenset(
    {"interrupted", "permission_denied", "approval_required", "policy", "crash"}
)


class Verdict(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalRef:
    id: str
    scope: str
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None = None
    revoked: bool = False

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise ValidationError("approval.id must be non-empty")
        if self.scope not in APPROVAL_SCOPES:
            raise ValidationError(
                f"approval.scope must be one of {sorted(APPROVAL_SCOPES)}"
            )
        if not str(self.granted_by or "").strip():
            raise ValidationError("approval.granted_by must be non-empty")


@dataclass(frozen=True)
class BlockReason:
    code: str
    detail: str
    resumable: bool
    recovery: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.code not in BLOCK_CODES:
            raise ValidationError(f"block.code must be one of {sorted(BLOCK_CODES)}")
        object.__setattr__(self, "recovery", deep_freeze(self.recovery))
