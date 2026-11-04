"""Explicit task state machine."""

from __future__ import annotations

from enum import Enum
from typing import Any

from .errors import IllegalTransition


class TaskState(str, Enum):
    CREATED = "created"
    PLANNED = "planned"
    APPROVED = "approved"
    ASSIGNED = "assigned"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"
    VERIFIED = "verified"
    REPORTED = "reported"
    FAILED = "failed"
    BLOCKED = "blocked"


TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    # CREATED->ASSIGNED skips separate planning: chain children and named
    # calls are assigned slices of an already-planned parent, not fresh plans.
    TaskState.CREATED: frozenset({TaskState.PLANNED, TaskState.ASSIGNED,
                                  TaskState.FAILED}),
    TaskState.PLANNED: frozenset(
        {TaskState.APPROVED, TaskState.NEEDS_APPROVAL, TaskState.FAILED}
    ),
    TaskState.NEEDS_APPROVAL: frozenset({TaskState.APPROVED, TaskState.FAILED}),
    TaskState.APPROVED: frozenset({TaskState.ASSIGNED, TaskState.FAILED}),
    TaskState.ASSIGNED: frozenset({TaskState.RUNNING, TaskState.FAILED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.NEEDS_APPROVAL,
            TaskState.VERIFIED,
            TaskState.BLOCKED,
            TaskState.FAILED,
        }
    ),
    TaskState.BLOCKED: frozenset({TaskState.RUNNING, TaskState.FAILED}),
    TaskState.VERIFIED: frozenset({TaskState.REPORTED, TaskState.FAILED}),
    TaskState.REPORTED: frozenset(),
    TaskState.FAILED: frozenset(),
}

TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.REPORTED, TaskState.FAILED}
)
RESUMABLE_STATES: frozenset[TaskState] = frozenset({TaskState.BLOCKED})
PENDING_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.CREATED,
        TaskState.PLANNED,
        TaskState.NEEDS_APPROVAL,
        TaskState.APPROVED,
        TaskState.ASSIGNED,
        TaskState.RUNNING,
    }
)


def legal_targets(state: TaskState) -> frozenset[TaskState]:
    """States reachable from ``state`` in one transition."""
    return TRANSITIONS.get(state, frozenset())


def can_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """True when ``from_state -> to_state`` is a legal transition."""
    return to_state in legal_targets(from_state)


def ensure_transition(task_id: str, from_state: Any, to_state: Any) -> None:
    """Raise ``IllegalTransition`` when the transition is not allowed."""
    if not can_transition(from_state, to_state):
        raise IllegalTransition(task_id, from_state, to_state)


def is_terminal(state: TaskState) -> bool:
    """True for terminal states (no outgoing transitions)."""
    return state in TERMINAL_STATES


def is_resumable_state(state: TaskState) -> bool:
    """True for states that may be resumed after a block."""
    return state in RESUMABLE_STATES


def is_pending_state(state: TaskState) -> bool:
    """True for states that still need action (not terminal, not blocked)."""
    return state in PENDING_STATES
