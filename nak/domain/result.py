"""Immutable task result."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from .errors import ValidationError
from .freeze import deep_freeze


class ResultStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_APPROVAL = "needs_approval"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Result:
    task_id: str
    worker: str
    status: ResultStatus
    summary: str
    files_changed: tuple[str, ...]
    commands_run: tuple[str, ...]
    evidence: MappingProxyType
    blockers: tuple[str, ...]
    needs_approval: bool

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise ValidationError("result.task_id must be non-empty")
        if not str(self.worker or "").strip():
            raise ValidationError("result.worker must be non-empty")
        if isinstance(self.status, ResultStatus):
            status = self.status
        else:
            try:
                status = ResultStatus(str(self.status))
            except ValueError as exc:
                raise ValidationError(
                    f"result.status {self.status!r} is invalid"
                ) from exc
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "files_changed", tuple(self.files_changed))
        object.__setattr__(self, "commands_run", tuple(self.commands_run))
        object.__setattr__(self, "evidence", deep_freeze(self.evidence))
        object.__setattr__(self, "blockers", tuple(self.blockers))
