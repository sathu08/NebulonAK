"""Phase A domain contract tests (pure, offline)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from nak.domain import (
    ApprovalRef,
    BlockReason,
    ConcurrencyError,
    ExecutionContext,
    IllegalTransition,
    Result,
    ResultStatus,
    Task,
    TaskRun,
    TaskState,
    ValidationError,
    can_transition,
    deep_freeze,
    ensure_transition,
    is_terminal,
)
from nak.domain.ports import ToolResult


def _task(**overrides):
    values = {
        "id": "t1",
        "idempotency_key": "k1",
        "goal": "build",
        "inputs": {"a": {"b": [1, {"c": 2}]}},
        "acceptance": ["tests pass"],
        "workspace": "/tmp/ws",
        "max_steps": 3,
        "timeout": 60,
        "allowed_tools": ["file"],
    }
    values.update(overrides)
    return Task(**values)


def test_deep_freeze_nested_containers():
    frozen = deep_freeze({"a": {"b": [1, {"c": 2}]}, "s": {1, 2}})
    assert frozen["a"]["b"][1]["c"] == 2
    assert isinstance(frozen["s"], frozenset)
    with pytest.raises(TypeError):
        frozen["a"]["b"][1]["c"] = 3


def test_task_is_immutable_and_deep_frozen():
    task = _task()
    assert task.allowed_tools == frozenset({"file"})
    assert task.inputs["a"]["b"][1]["c"] == 2
    with pytest.raises(FrozenInstanceError):
        task.goal = "changed"
    with pytest.raises(TypeError):
        task.inputs["a"] = {}


def test_task_rejects_bad_values():
    with pytest.raises(ValidationError):
        _task(id="")
    with pytest.raises(ValidationError):
        _task(max_steps=0)
    with pytest.raises(ValidationError):
        _task(inputs=["not", "a", "mapping"])


def test_legal_and_illegal_transitions():
    assert can_transition(TaskState.CREATED, TaskState.PLANNED)
    assert not can_transition(TaskState.REPORTED, TaskState.RUNNING)
    ensure_transition("t1", TaskState.PLANNED, TaskState.APPROVED)
    with pytest.raises(IllegalTransition):
        ensure_transition("t1", TaskState.REPORTED, TaskState.RUNNING)
    assert is_terminal(TaskState.REPORTED)
    assert is_terminal(TaskState.FAILED)
    assert not is_terminal(TaskState.RUNNING)


def test_task_run_resume_requires_resumable_block():
    run = TaskRun(
        task_id="t1",
        state=TaskState.BLOCKED,
        version=0,
        assigned_worker="arivu",
        attempt=1,
        block_reason=BlockReason(
            code="interrupted",
            detail="restart",
            resumable=True,
            recovery={"step": 2},
        ),
    )
    assert run.can_resume()
    assert run.block_reason.recovery["step"] == 2
    denied = TaskRun(
        task_id="t1",
        state=TaskState.BLOCKED,
        version=0,
        assigned_worker="arivu",
        attempt=1,
        block_reason=BlockReason(
            code="permission_denied", detail="no", resumable=False
        ),
    )
    assert not denied.can_resume()


def test_result_status_enum_and_frozen_evidence():
    result = Result(
        task_id="t1",
        worker="arivu",
        status=ResultStatus.OK,
        summary="done",
        files_changed=["a.py"],
        commands_run=["pytest"],
        evidence={"tests": {"passed": 3}},
        blockers=[],
        needs_approval=False,
    )
    assert result.status is ResultStatus.OK
    assert result.files_changed == ("a.py",)
    with pytest.raises(TypeError):
        result.evidence["tests"] = {}


def test_approval_ref_validation():
    ref = ApprovalRef(
        id="a1",
        scope="task",
        granted_by="sathya",
        granted_at=datetime.now(timezone.utc),
    )
    assert ref.revoked is False
    with pytest.raises(ValidationError):
        ApprovalRef(
            id="a1",
            scope="bogus",
            granted_by="sathya",
            granted_at=datetime.now(timezone.utc),
        )


def test_concurrency_error_message():
    err = ConcurrencyError("t1", expected=1, actual=2)
    assert "version conflict" in str(err)


def test_execution_context_coerces_tools():
    class _Policy:
        def verdict(self, worker, tool, capability, resource, ctx):
            return "allow"

    class _Authority:
        def is_valid(self, ref, ctx):
            return True

    class _Cancel:
        @property
        def cancelled(self):
            return False

        def raise_if_cancelled(self):
            return None

    ctx = ExecutionContext(
        task_id="t1",
        worker_id="arivu",
        workspace="/tmp/ws",
        allowed_tools=["file"],
        attempt=1,
        approval=None,
        policy=_Policy(),
        authority=_Authority(),
        cancel=_Cancel(),
    )
    assert ctx.allowed_tools == frozenset({"file"})


def test_tool_result_freezes_data():
    result = ToolResult(ok=True, data={"x": {"y": 1}})
    assert result.data["x"]["y"] == 1
    with pytest.raises(TypeError):
        result.data["x"] = {}
