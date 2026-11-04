"""NebulonDB adapter tests against a faithful fake server (offline).

The fake mirrors the real cosmos route semantics: inserts spread metadata
into doc fields, ``created_at``/``type`` are server-set, ``_id`` values are
server-assigned, and ``create_corpus`` reports ``exists=True`` for dupes.
"""

from __future__ import annotations

import pytest
import requests

from nak.adapters.store_nebulondb import NDBError, NebulonDBTaskStore, _NDBClient
from nak.domain import (
    ConcurrencyError,
    DuplicateTask,
    Result,
    ResultStatus,
    Task,
    TaskState,
    TaskNotFound,
)


class FakeNDBServer:
    """Minimal cosmos behavior: corpus bootstrap, insert, whole-segment read."""

    def __init__(self):
        self.corpora = set()
        self.segments = {}
        self._ids = 0
        self.calls = []
        self.auth = ("", "")
        self.base = ""

    def set_auth(self, user, password):
        self.auth = (user, password)

    def set_base(self, base_url):
        self.base = base_url

    def post(self, path, payload):
        self.calls.append((path, dict(payload)))
        if path == "/corpus/create_corpus":
            name = payload["corpus_name"]
            if name in self.corpora:
                return {"success": False, "exists": True,
                        "message": f"Corpus '{name}' already exists in the database"}
            self.corpora.add(name)
            return {"success": True, "exists": False, "message": "created"}
        if path == "/segment/get_data":
            docs = self.segments.get(
                (payload["corpus_name"], payload["segment_name"]), [])
            return {"success": True,
                    "data": {"records": [dict(d) for d in docs],
                             "total_count": len(docs), "limit": None}}
        if path == "/segment/add_records":
            key = (payload["corpus_name"], payload["segment_name"])
            seg = self.segments.setdefault(key, [])
            ids = []
            for rec in payload["records"]:
                self._ids += 1
                meta = dict(rec.get("metadata") or {})
                doc = {"text": rec.get("text") or "",
                       "type": meta.get("type", "other"),
                       **meta, "created_at": "2026-01-01T00:00:00+00:00",
                       "_id": self._ids}
                seg.append(doc)
                ids.append(self._ids)
            return {"success": True,
                    "data": {"inserted": len(ids), "record_ids": ids}}
        raise AssertionError(f"unexpected path {path}")


def _task(**overrides):
    values = {
        "id": "t1",
        "idempotency_key": "k1",
        "goal": "build it",
        "inputs": {"nested": {"items": [1, 2]}},
        "acceptance": ["tests pass"],
        "workspace": "/tmp/ws",
        "max_steps": 3,
        "timeout": 60,
        "allowed_tools": ["file", "terminal"],
    }
    values.update(overrides)
    return Task(**values)


def _store(server=None):
    return NebulonDBTaskStore(client=server or FakeNDBServer())


def test_bootstrap_is_idempotent():
    server = FakeNDBServer()
    first = _store(server)
    first.create_task(_task())
    second = _store(server)  # corpus already exists -> exists=True path
    found = second.get_task("t1")
    assert found[0].id == "t1"


def test_create_get_roundtrip_with_nested_inputs():
    store = _store()
    store.create_task(_task())
    task, run = store.get_task("t1")
    assert task.goal == "build it"
    # JSON semantics: sequences round-trip as frozen tuples on read.
    assert task.inputs["nested"]["items"] == (1, 2)
    assert task.allowed_tools == frozenset({"file", "terminal"})
    assert run.state == TaskState.CREATED
    assert run.version == 0
    assert store.get_task("missing") is None


def test_docs_avoid_reserved_keys():
    server = FakeNDBServer()
    store = _store(server)
    store.create_task(_task())
    for docs in server.segments.values():
        for doc in docs:
            assert "id" not in doc
            assert doc.get("task_id") == "t1"
    kinds = {d.get("kind") for docs in server.segments.values() for d in docs}
    assert kinds == {"task", "run", "event"}


def test_duplicate_key_and_transition_flow():
    store = _store()
    store.create_task(_task())
    with pytest.raises(DuplicateTask):
        store.create_task(_task(id="t2"))
    assert store.get_task_by_idempotency("k1")[0].id == "t1"
    run = store.transition("t1", 0, TaskState.PLANNED, {"type": "task.planned"})
    assert (run.state, run.version) == (TaskState.PLANNED, 1)
    with pytest.raises(ConcurrencyError):
        store.transition("t1", 0, TaskState.APPROVED, {})
    with pytest.raises(TaskNotFound):
        store.transition("ghost", 0, TaskState.PLANNED, {})


def test_crash_between_event_and_snapshot_heals():
    server = FakeNDBServer()
    store = _store(server)
    store.create_task(_task())
    store.transition("t1", 0, TaskState.PLANNED, {"type": "task.planned"})
    # Simulate a crash after the event insert: drop the latest snapshot.
    runs = server.segments[("nak_tasks", "task_runs")]
    dropped = runs.pop()
    assert dropped["version"] == 1
    # Simulate a process restart: a cold cache replays from the server.
    restarted = _store(server)
    _, run = restarted.get_task("t1")
    assert run.state == TaskState.PLANNED
    assert run.version == 1
    # The healed snapshot was persisted, not just returned.
    assert any(d.get("version") == 1
               for d in server.segments[("nak_tasks", "task_runs")])


def test_recovery_marks_interrupted():
    store = _store()
    store.create_task(_task())
    run = store.transition("t1", 0, TaskState.PLANNED, {})
    run = store.transition("t1", run.version, TaskState.APPROVED, {})
    run = store.transition("t1", run.version, TaskState.ASSIGNED, {})
    store.transition("t1", run.version, TaskState.RUNNING, {})
    recovered = store.recover_incomplete()
    assert [r.task_id for r in recovered] == ["t1"]
    _, run = store.get_task("t1")
    assert run.state == TaskState.BLOCKED
    assert run.block_reason.code == "interrupted"
    assert not run.can_resume()


def test_save_result_roundtrip():
    store = _store()
    store.create_task(_task())
    store.save_result("t1", Result(
        task_id="t1", worker="ezhilan", status=ResultStatus.FAILED,
        summary="2 failed", files_changed=[], commands_run=["pytest"],
        evidence={"failed": ["a", "b"]}, blockers=["a"], needs_approval=False))
    _, run = store.get_task("t1")
    assert run.result.status == ResultStatus.FAILED
    # Evidence is deep-frozen on read: lists round-trip as tuples.
    assert run.result.evidence["failed"] == ("a", "b")


def test_server_error_maps_to_ndb_error():
    class Failing(FakeNDBServer):
        def post(self, path, payload):
            if path == "/segment/add_records":
                return {"success": False, "message": "boom"}
            return super().post(path, payload)

    store = _store(Failing())
    with pytest.raises(NDBError):
        store.create_task(_task())


class _Http:
    def __init__(self, status, body=None):
        self.status_code = status
        self.text = "err"
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def test_client_retries_transient_then_succeeds(monkeypatch):
    calls = []
    plan = [_Http(503), _Http(503), _Http(200, {"success": True, "data": {}})]

    def fake_post(*args, **kwargs):
        calls.append(1)
        return plan[len(calls) - 1]

    monkeypatch.setattr(requests, "post", fake_post)
    client = _NDBClient("http://x", "u", "p", max_retries=3, retry_backoff=0)
    assert client.post("/x", {}) == {"success": True, "data": {}}
    assert len(calls) == 3


def test_client_no_retry_on_auth(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return _Http(401)

    monkeypatch.setattr(requests, "post", fake_post)
    client = _NDBClient("http://x", "u", "p", max_retries=3, retry_backoff=0)
    with pytest.raises(NDBError):
        client.post("/x", {})
    assert len(calls) == 1


def test_client_gives_up_after_retries(monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "post", fake_post)
    client = _NDBClient("http://x", "u", "p", max_retries=2, retry_backoff=0)
    with pytest.raises(NDBError):
        client.post("/x", {})
