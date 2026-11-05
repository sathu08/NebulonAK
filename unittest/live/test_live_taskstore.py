"""Live NebulonDB TaskStore test (skips when no DB is reachable).

Requires NebulonDB up with an admin user:
    NAK_NDB_BASE_URL=http://localhost:6969/api/NebulonDB
    NAK_NDB_USER=<admin>  NAK_NDB_PASSWORD=<secret>
Uses a dedicated test corpus so real task data is never touched.
"""

from __future__ import annotations

import os
import uuid

import pytest

from nak_host.adapters.store_nebulondb import NDBError, NebulonDBTaskStore
from nak_host.domain import Task, TaskState


def _settings():
    return {
        "base_url": os.environ.get("NAK_NDB_BASE_URL",
                                   "http://localhost:6969/api/NebulonDB"),
        "user": os.environ.get("NAK_NDB_USER", ""),
        "password": os.environ.get("NAK_NDB_PASSWORD", ""),
        "corpus": "nak_tasks_test",
    }


def _store():
    cfg = _settings()
    return NebulonDBTaskStore(base_url=cfg["base_url"], user=cfg["user"],
                              password=cfg["password"], corpus=cfg["corpus"],
                              timeout=10.0, max_retries=1)


def test_live_task_lifecycle():
    store = _store()
    suffix = uuid.uuid4().hex[:8]
    try:
        task = Task(
            id=f"live-{suffix}", idempotency_key=f"live-key-{suffix}",
            goal="live check", inputs={}, acceptance=("ok",),
            workspace="/tmp/ws", max_steps=1, timeout=60,
            allowed_tools=frozenset())
        run = store.create_task(task)
        assert run.state == TaskState.CREATED
        run = store.transition(task.id, run.version, TaskState.PLANNED,
                               {"type": "task.planned"})
        assert run.version == 1
        found_task, found_run = store.get_task(task.id)
        assert found_task.id == task.id
        assert found_run.state == TaskState.PLANNED
        assert store.get_task_by_idempotency(f"live-key-{suffix}")[0].id == task.id
    except NDBError as exc:
        pytest.skip(f"NebulonDB unavailable: {exc}")
