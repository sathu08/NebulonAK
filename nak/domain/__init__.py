"""nak.domain — pure domain contracts (no IO, no adapters).

Phase A of ``ARCHITECTURE.md``: immutable Task data, mutable TaskRun state,
typed ports and the explicit state machine. This package imports nothing
outward; adapters implement the ports.
"""

from .errors import (
    ApprovalRevoked,
    ConcurrencyError,
    DomainError,
    DuplicateTask,
    IllegalTransition,
    PermissionDenied,
    TaskCancelled,
    TaskNotFound,
    ValidationError,
)
from .freeze import deep_freeze
from .permission import (
    APPROVAL_SCOPES,
    BLOCK_CODES,
    ApprovalRef,
    BlockReason,
    Verdict,
)
from .ports import (
    ApprovalAuthority,
    ApproverPort,
    BrainPort,
    CancellationToken,
    ExecutionContext,
    MemoryPort,
    PermissionPolicy,
    RegistryPort,
    TaskStorePort,
    ToolExecutorPort,
    ToolResult,
)
from .result import Result, ResultStatus
from .state import (
    PENDING_STATES,
    RESUMABLE_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    TaskState,
    can_transition,
    ensure_transition,
    is_pending_state,
    is_resumable_state,
    is_terminal,
    legal_targets,
)
from .task import Task, TaskRun

__all__ = [
    "APPROVAL_SCOPES",
    "BLOCK_CODES",
    "PENDING_STATES",
    "RESUMABLE_STATES",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "ApprovalAuthority",
    "ApprovalRef",
    "ApprovalRevoked",
    "ApproverPort",
    "BlockReason",
    "BrainPort",
    "CancellationToken",
    "ConcurrencyError",
    "DomainError",
    "DuplicateTask",
    "ExecutionContext",
    "IllegalTransition",
    "MemoryPort",
    "PermissionDenied",
    "PermissionPolicy",
    "RegistryPort",
    "Result",
    "ResultStatus",
    "Task",
    "TaskCancelled",
    "TaskNotFound",
    "TaskRun",
    "TaskState",
    "TaskStorePort",
    "ToolExecutorPort",
    "ToolResult",
    "ValidationError",
    "Verdict",
    "can_transition",
    "deep_freeze",
    "ensure_transition",
    "is_pending_state",
    "is_resumable_state",
    "is_terminal",
    "legal_targets",
]
