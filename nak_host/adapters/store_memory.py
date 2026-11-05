"""In-memory TaskStorePort: process-local default for tests/offline use.

Same semantics as the durable adapters (legal transitions, optimistic
concurrency, idempotency, replayable events) with no backend. Reference
implementation for the port contract tests.
"""

from __future__ import annotations

import threading

from dataclasses import replace
from typing import Any

from nak_host.domain.errors import ConcurrencyError, DuplicateTask, TaskNotFound
from nak_host.domain.state import (
    TaskState,
    ensure_transition,
    is_pending_state,
)
from nak_host.domain.permission import BlockReason
from nak_host.domain.result import Result
from nak_host.domain.task import Task, TaskRun
from nak_host.utils.constant import STORE_RUN_FIELDS as _RUN_FIELDS


class InMemoryTaskStore:
    """Single-writer in-memory store. Not shared across processes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._by_key: dict[str, str] = {}
        self._runs: dict[str, TaskRun] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._results: dict[tuple[str, int], Result] = {}
        self._attempt_of: dict[str, int] = {}

    # -- reads -------------------------------------------------------- #
    def _copy(self, run: TaskRun) -> TaskRun:
        return replace(run)

    def get_task(self, task_id: str) -> tuple[Task, TaskRun] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            run = self._runs.get(task_id)
            if task is None or run is None:
                return None
            latest = self._results.get((task_id, self._attempt_of.get(task_id, 1)))
            if latest is not None and run.result is None:
                run = replace(run, result=latest)
            return task, self._copy(run)

    def get_task_by_idempotency(self, idempotency_key: str) -> tuple[Task, TaskRun] | None:
        with self._lock:
            task_id = self._by_key.get(str(idempotency_key or ""))
            if not task_id:
                return None
        return self.get_task(task_id)

    def list_pending(self) -> list[TaskRun]:
        with self._lock:
            return [self._copy(r) for r in self._runs.values()
                    if is_pending_state(r.state)]

    def events_for(self, task_id: str) -> list[dict[str, Any]]:
        """Stored events (oldest first). Test/observation helper."""
        with self._lock:
            return [dict(e) for e in self._events.get(task_id, [])]

    # -- writes ------------------------------------------------------- #
    def create_task(self, task: Task) -> TaskRun:
        with self._lock:
            if task.idempotency_key in self._by_key:
                raise DuplicateTask(
                    f"task with idempotency key {task.idempotency_key!r} already exists"
                )
            if task.id in self._tasks:
                raise DuplicateTask(f"task {task.id!r} already exists")
            self._tasks[task.id] = task
            self._by_key[task.idempotency_key] = task.id
            run = TaskRun(task_id=task.id, state=TaskState.CREATED,
                          version=0, assigned_worker=None, attempt=1)
            self._runs[task.id] = run
            self._events.setdefault(task.id, [])
            self._attempt_of[task.id] = 1
            return self._copy(run)

    def append_event(self, task_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            if task_id not in self._tasks:
                raise TaskNotFound(f"unknown task {task_id!r}")
            seq = len(self._events.setdefault(task_id, [])) + 1
            self._events[task_id].append({"seq": seq, **dict(event)})

    def transition(self, task_id: str, expected_version: int,
                   to_state: TaskState, event: dict[str, Any],
                   **fields: Any) -> TaskRun:
        unknown = [k for k in fields if k not in _RUN_FIELDS]
        if unknown:
            raise ValueError(f"unknown run fields: {unknown}")
        with self._lock:
            run = self._runs.get(task_id)
            if run is None or task_id not in self._tasks:
                raise TaskNotFound(f"unknown task {task_id!r}")
            if run.version != int(expected_version):
                raise ConcurrencyError(task_id, int(expected_version), run.version)
            ensure_transition(task_id, run.state, to_state)
            old_state = run.state
            for key, value in fields.items():
                setattr(run, key, value)
            run.state = to_state
            run.version = run.version + 1
            seq = len(self._events.setdefault(task_id, [])) + 1
            self._events[task_id].append({
                "seq": seq, "type": "task.state_changed",
                "from_state": old_state.value, "to_state": to_state.value,
                **dict(event),
            })
            return self._copy(run)

    def save_result(self, task_id: str, result: Result) -> None:
        with self._lock:
            if task_id not in self._tasks:
                raise TaskNotFound(f"unknown task {task_id!r}")
            attempt = self._attempt_of.get(task_id, 1)
            if (task_id, attempt) in self._results:
                raise DuplicateTask(
                    f"result for task {task_id!r} attempt {attempt} already saved"
                )
            self._results[(task_id, attempt)] = result

    def recover_incomplete(self) -> list[TaskRun]:
        recovered: list[TaskRun] = []
        with self._lock:
            stuck = [r for r in self._runs.values()
                     if r.state == TaskState.RUNNING]
        for run in stuck:
            reason = BlockReason(
                code="interrupted", detail="recovered on startup",
                resumable=False,
                recovery={"state": TaskState.RUNNING.value,
                          "version": run.version,
                          "worker": run.assigned_worker or ""},
            )
            try:
                recovered.append(self.transition(
                    run.task_id, run.version, TaskState.BLOCKED,
                    {"type": "task.interrupted"}, block_reason=reason))
            except (ConcurrencyError, TaskNotFound):
                # Lost a race with another transition; state already moved on.
                continue
        return recovered
