"""Domain error taxonomy (pure; no IO)."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for every domain error."""


class ValidationError(DomainError):
    """A domain value failed validation."""


class IllegalTransition(DomainError):
    """A task state transition is not allowed."""

    def __init__(self, task_id: str, from_state: Any, to_state: Any) -> None:
        self.task_id = task_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"task {task_id!r}: illegal transition {from_state!r} -> {to_state!r}"
        )


class ConcurrencyError(DomainError):
    """Optimistic concurrency check failed."""

    def __init__(self, task_id: str, expected: int, actual: int) -> None:
        self.task_id = task_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"task {task_id!r}: version conflict (expected {expected}, actual {actual})"
        )


class TaskNotFound(DomainError):
    """No task with the given id exists."""


class DuplicateTask(DomainError):
    """A task with the same idempotency key already exists."""


class PermissionDenied(DomainError):
    """A tool call was denied by the permission policy."""


class ApprovalRevoked(DomainError):
    """An approval reference is no longer valid."""


class TaskCancelled(DomainError):
    """The run was cancelled."""
