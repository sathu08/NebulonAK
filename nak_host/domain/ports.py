"""Port contracts and the execution context."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from .errors import ValidationError
from .freeze import deep_freeze
from .permission import ApprovalRef, Verdict
from .result import Result
from .state import TaskState
from .task import Task, TaskRun


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", deep_freeze(self.data))


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool:
        ...

    def raise_if_cancelled(self) -> None:
        ...


class PermissionPolicy(Protocol):
    def verdict(
        self,
        worker: str,
        tool: str,
        capability: str,
        resource: str | None,
        ctx: ExecutionContext,
    ) -> Verdict:
        ...


class ApprovalAuthority(Protocol):
    def is_valid(self, ref: ApprovalRef | None, ctx: ExecutionContext) -> bool:
        ...


@dataclass(frozen=True)
class ExecutionContext:
    task_id: str
    worker_id: str
    workspace: str
    allowed_tools: frozenset[str]
    attempt: int
    approval: ApprovalRef | None
    policy: PermissionPolicy
    authority: ApprovalAuthority
    cancel: CancellationToken

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise ValidationError("context.task_id must be non-empty")
        if not str(self.worker_id or "").strip():
            raise ValidationError("context.worker_id must be non-empty")
        if not str(self.workspace or "").strip():
            raise ValidationError("context.workspace must be non-empty")
        object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))


class ToolExecutorPort(Protocol):
    def inspect(
        self, tool: str, args: dict[str, Any], ctx: ExecutionContext
    ) -> Verdict:
        ...

    def run(
        self, tool: str, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        ...


class BrainPort(Protocol):
    def chat(self, text: str, **kwargs: Any) -> dict[str, Any]:
        ...

    def search(self, **kwargs: Any) -> dict[str, Any]:
        ...

    def decide(self, **kwargs: Any) -> dict[str, Any]:
        ...


class RegistryPort(Protocol):
    def workers(self) -> list[Any]:
        ...

    def teams(self) -> list[Any]:
        ...

    def crafts(self) -> list[Any]:
        ...

    def tools(self) -> list[Any]:
        ...

    def permissions(self) -> Any:
        ...


class MemoryPort(Protocol):
    def recall(self, query: str, **kwargs: Any) -> dict[str, Any]:
        ...

    def store(self, **kwargs: Any) -> dict[str, Any]:
        ...


class ApproverPort(Protocol):
    def __call__(self, info: dict[str, Any]) -> bool:
        ...


class TaskStorePort(Protocol):
    def create_task(self, task: Task) -> TaskRun:
        ...

    def get_task(self, task_id: str) -> tuple[Task, TaskRun] | None:
        ...

    def get_task_by_idempotency(self, idempotency_key: str) -> tuple[Task, TaskRun] | None:
        ...

    def transition(
        self,
        task_id: str,
        expected_version: int,
        to_state: TaskState,
        event: dict[str, Any],
        **fields: Any,
    ) -> TaskRun:
        ...

    def append_event(self, task_id: str, event: dict[str, Any]) -> None:
        ...

    def save_result(self, task_id: str, result: Result) -> None:
        ...

    def list_pending(self) -> list[TaskRun]:
        ...

    def recover_incomplete(self) -> list[TaskRun]:
        ...
