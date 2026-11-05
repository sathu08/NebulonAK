"""Create a task: validate, dedupe, persist. Store-agnostic."""

from __future__ import annotations

import uuid

from typing import Any

from nak_host.domain.errors import DuplicateTask
from nak_host.domain.task import Task, TaskRun


def create_task(goal: str, *, store: Any, task_id: str | None = None,
                idempotency_key: str | None = None,
                inputs: Any = None, acceptance: Any = (),
                workspace: str = "", max_steps: int = 3, timeout: int = 60,
                allowed_tools: Any = frozenset()) -> tuple[Task, TaskRun]:
    """Build a Task, persist it, and record the creation event.

    A duplicate ``idempotency_key`` returns the existing (Task, TaskRun)
    instead of creating a second run.
    """
    g = str(goal or "").strip()
    if not g:
        raise ValueError("goal must be non-empty")
    key = str(idempotency_key or "").strip() or uuid.uuid4().hex
    found = store.get_task_by_idempotency(key)
    if found is not None:
        return found
    task = Task(
        id=str(task_id or "").strip() or uuid.uuid4().hex,
        idempotency_key=key,
        goal=g,
        inputs=dict(inputs or {}),
        acceptance=tuple(acceptance or ()),
        workspace=str(workspace or "").strip(),
        max_steps=int(max_steps),
        timeout=int(timeout),
        allowed_tools=frozenset(allowed_tools or ()),
    )
    try:
        run = store.create_task(task)
    except DuplicateTask:
        # Lost a race with another creator; return the winner.
        found = store.get_task_by_idempotency(key)
        if found is not None:
            return found
        raise
    store.append_event(task.id, {"type": "task.created", "goal": g})
    return task, run
