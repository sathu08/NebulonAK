"""Task (immutable input) and TaskRun (mutable execution state)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .errors import ValidationError
from .freeze import deep_freeze
from .permission import BlockReason
from .result import Result
from .state import TaskState, is_resumable_state


@dataclass(frozen=True)
class Task:
    id: str
    idempotency_key: str
    goal: str
    inputs: MappingProxyType
    acceptance: tuple[str, ...]
    workspace: str
    max_steps: int
    timeout: int
    allowed_tools: frozenset[str]

    def __post_init__(self) -> None:
        for name in ("id", "idempotency_key", "goal", "workspace"):
            if not str(getattr(self, name) or "").strip():
                raise ValidationError(f"task.{name} must be non-empty")
        if not isinstance(self.inputs, Mapping):
            raise ValidationError("task.inputs must be a mapping")
        if isinstance(self.acceptance, str):
            raise ValidationError("task.acceptance must be a sequence, not a string")
        if isinstance(self.allowed_tools, str):
            raise ValidationError("task.allowed_tools must be a set, not a string")
        try:
            max_steps = int(self.max_steps)
            timeout = int(self.timeout)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "task.max_steps and task.timeout must be integers"
            ) from exc
        if max_steps < 1:
            raise ValidationError("task.max_steps must be >= 1")
        if timeout < 1:
            raise ValidationError("task.timeout must be >= 1")
        object.__setattr__(self, "max_steps", max_steps)
        object.__setattr__(self, "timeout", timeout)
        object.__setattr__(self, "inputs", deep_freeze(self.inputs))
        object.__setattr__(self, "acceptance", tuple(self.acceptance))
        object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))


@dataclass
class TaskRun:
    task_id: str
    state: TaskState
    version: int
    assigned_worker: str | None
    attempt: int
    block_reason: BlockReason | None = None
    result: Result | None = None

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise ValidationError("task run task_id must be non-empty")
        if not isinstance(self.state, TaskState):
            raise ValidationError("task run state must be a TaskState")
        try:
            version = int(self.version)
            attempt = int(self.attempt)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "task run version/attempt must be integers"
            ) from exc
        if version < 0:
            raise ValidationError("task run version must be >= 0")
        if attempt < 1:
            raise ValidationError("task run attempt must be >= 1")
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "attempt", attempt)

    def can_resume(self) -> bool:
        """True when a blocked run has a resumable reason."""
        return (
            is_resumable_state(self.state)
            and self.block_reason is not None
            and self.block_reason.resumable
        )
