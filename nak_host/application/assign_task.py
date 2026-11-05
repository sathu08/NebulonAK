"""Assign a planned task to workers (records the assignment).

``splitter`` is an injected ``(team_spec, assigned, exclude) ->
(allowed, blocked)`` callable (wiring passes ``split_assignments``), so this
module never imports roster code. The ``ASSIGNED`` transition only happens
from ``APPROVED``; anything else (e.g. ``NEEDS_APPROVAL``) still returns the
split so callers can report blocked names.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from nak_host.domain.state import TaskState
from nak_host.domain.task import Task, TaskRun


def assign_task(task: Task, run: TaskRun, *, store: Any,
                team_spec: dict[str, Any] | None,
                assigned: list[str] | None, planner_name: str,
                splitter: Callable[..., Any]) -> dict[str, Any]:
    """Split assignments into roster-allowed and blocked, then record."""
    allowed, blocked = splitter(team_spec, assigned or [], planner_name)
    if run.state == TaskState.APPROVED:
        run = store.transition(task.id, run.version, TaskState.ASSIGNED,
                               {"type": "task.assigned",
                                "allowed": list(allowed), "blocked": list(blocked)})
    return {"assigned_workers": list(assigned or []), "allowed": list(allowed),
            "blocked": list(blocked), "run": run}
