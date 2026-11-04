"""TaskStorePort contract tests against the in-memory store (offline)."""

from __future__ import annotations

import pytest

from nak.adapters.store_memory import InMemoryTaskStore
from nak.domain import (
    BlockReason,
    ConcurrencyError,
    DuplicateTask,
    IllegalTransition,
    Result,
    ResultStatus,
    Task,
    TaskRun,
    TaskState,
    TaskNotFound,
)


def _task(**overrides):
    values = {
        "id": "t1",
        "idempotency_key": "k1",
        "goal": "build it",
        "inputs": {"a": 1},
        "acceptance": ["tests pass"],
        "workspace": "/tmp/ws",
        "max_steps": 3,
        "timeout": 60,
        "allowed_tools": ["file"],
    }
    values.update(overrides)
    return Task(**values)


def _result(**overrides):
    values = {
        "task_id": "t1",
        "worker": "arivu",
        "status": ResultStatus.OK,
        "summary": "done",
        "files_changed": ["a.py"],
        "commands_run": ["pytest"],
        "evidence": {"passed": 3},
        "blockers": [],
        "needs_approval": False,
    }
    values.update(overrides)
    return Result(**values)


def test_create_get_roundtrip():
    store = InMemoryTaskStore()
    task = _task()
    run = store.create_task(task)
    assert run.state == TaskState.CREATED
    assert run.version == 0
    found_task, found_run = store.get_task("t1")
    assert found_task == task
    assert found_run.state == TaskState.CREATED
    assert store.get_task("missing") is None


def test_duplicate_idempotency_key():
    store = InMemoryTaskStore()
    store.create_task(_task())
    with pytest.raises(DuplicateTask):
        store.create_task(_task(id="t2"))
    found = store.get_task_by_idempotency("k1")
    assert found[0].id == "t1"
    assert store.get_task_by_idempotency("nope") is None


def test_legal_transition_bumps_version_and_event():
    store = InMemoryTaskStore()
    store.create_task(_task())
    run = store.transition("t1", 0, TaskState.PLANNED, {"type": "task.planned"})
    assert run.state == TaskState.PLANNED
    assert run.version == 1
    events = store.events_for("t1")
    assert [e["type"] for e in events] == ["task.planned"]
    assert events[0]["from_state"] == "created"
    assert events[0]["to_state"] == "planned"


def test_illegal_transition_rejected():
    store = InMemoryTaskStore()
    store.create_task(_task())
    with pytest.raises(IllegalTransition):
        store.transition("t1", 0, TaskState.RUNNING, {})
    # state untouched, no event recorded
    assert store.get_task("t1")[1].version == 0
    assert store.events_for("t1") == []


def test_version_conflict():
    store = InMemoryTaskStore()
    store.create_task(_task())
    store.transition("t1", 0, TaskState.PLANNED, {})
    with pytest.raises(ConcurrencyError):
        store.transition("t1", 0, TaskState.APPROVED, {})
    with pytest.raises(TaskNotFound):
        store.transition("ghost", 0, TaskState.PLANNED, {})


def test_unknown_run_fields_rejected():
    store = InMemoryTaskStore()
    store.create_task(_task())
    with pytest.raises(ValueError):
        store.transition("t1", 0, TaskState.PLANNED, {}, bogus=1)


def test_save_result_latest_attached():
    store = InMemoryTaskStore()
    store.create_task(_task())
    store.save_result("t1", _result())
    _, run = store.get_task("t1")
    assert run.result is not None
    assert run.result.status == ResultStatus.OK
    assert run.result.evidence["passed"] == 3
    with pytest.raises(DuplicateTask):
        store.save_result("t1", _result())
    with pytest.raises(TaskNotFound):
        store.save_result("ghost", _result())


def test_list_pending_and_recovery():
    store = InMemoryTaskStore()
    store.create_task(_task())
    store.create_task(_task(id="t2", idempotency_key="k2"))
    assert {r.task_id for r in store.list_pending()} == {"t1", "t2"}
    run = store.transition("t1", 0, TaskState.PLANNED, {})
    run = store.transition("t1", run.version, TaskState.APPROVED, {})
    run = store.transition("t1", run.version, TaskState.ASSIGNED,
                           {}, assigned_worker="arivu")
    assert run.assigned_worker == "arivu"
    run = store.transition("t1", run.version, TaskState.RUNNING, {})
    recovered = store.recover_incomplete()
    assert [r.task_id for r in recovered] == ["t1"]
    _, run = store.get_task("t1")
    assert run.state == TaskState.BLOCKED
    assert run.block_reason.code == "interrupted"
    assert not run.can_resume()
    # t2 still pending, t1 no longer pending
    assert [r.task_id for r in store.list_pending()] == ["t2"]


def test_block_reason_roundtrip():
    store = InMemoryTaskStore()
    store.create_task(_task())
    run = store.transition("t1", 0, TaskState.PLANNED, {})
    run = store.transition("t1", run.version, TaskState.APPROVED, {})
    run = store.transition("t1", run.version, TaskState.ASSIGNED, {})
    run = store.transition("t1", run.version, TaskState.RUNNING, {})
    reason = BlockReason(code="policy", detail="no", resumable=False,
                         recovery={"step": 1})
    run = store.transition("t1", run.version, TaskState.BLOCKED,
                           {"type": "x"}, block_reason=reason)
    assert isinstance(run, TaskRun)
    assert run.block_reason.code == "policy"
    assert run.block_reason.recovery["step"] == 1
