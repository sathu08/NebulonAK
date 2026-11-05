"""Memory + harness-executor adapter tests (offline)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from nak_host.adapters.executor_harness import HarnessExecutor
from nak_host.adapters.memory_nebulon import NebulonMemory
from nak_host.domain import ApprovalRef, ExecutionContext, Verdict
from nak_host.domain.errors import ApprovalRevoked, TaskCancelled


class _Brain:
    def __init__(self):
        self.searches = []
        self.saved = []
        self.remembered = []

    def search(self, query, top_k=5):
        self.searches.append((query, top_k))
        return [{"text": "hit1"}, {"text": "hit2"}]

    def save_chat(self, doc, user=None):
        self.saved.append({"doc": doc, "user": user})
        return {"ok": True, "id": "c1"}

    def remember(self, text, user=None, **kwargs):
        self.remembered.append({"text": text, "user": user, **kwargs})
        return {"ok": True}


class _Authority:
    def __init__(self, valid=True):
        self._valid = valid

    def is_valid(self, ref, ctx):
        return self._valid


class _Cancel:
    def __init__(self, cancelled=False):
        self._cancelled = cancelled

    @property
    def cancelled(self):
        return self._cancelled

    def raise_if_cancelled(self):
        if self._cancelled:
            raise TaskCancelled("cancelled by test")


def _approval():
    return ApprovalRef(id="a1", scope="task", granted_by="tester",
                       granted_at=datetime.now(timezone.utc))


def _ctx(worker="vizhi", approval=None, valid=True, cancelled=False,
         allowed=()):
    return ExecutionContext(
        task_id="t1", worker_id=worker, workspace="/tmp/ws",
        allowed_tools=frozenset(allowed), attempt=1, approval=approval,
        policy=object(), authority=_Authority(valid),
        cancel=_Cancel(cancelled))


def test_recall_shape_and_passthrough():
    brain = _Brain()
    mem = NebulonMemory(brain, user="tester")
    out = mem.recall("invoices", top_k=2)
    assert out == {"query": "invoices", "hits": [{"text": "hit1"}, {"text": "hit2"}],
                   "count": 2}
    assert brain.searches == [("invoices", 2)]
    with pytest.raises(ValueError):
        mem.recall("   ")
    with pytest.raises(RuntimeError):
        NebulonMemory().recall("x")


def test_store_turn_text_facts():
    brain = _Brain()
    mem = NebulonMemory(brain, user="tester")
    out = mem.store(turn={"id": "c1", "messages": []})
    assert out["saved"] == {"ok": True, "id": "c1"}
    assert brain.saved[0]["user"] == "tester"
    out = mem.store(text="vizhi likes deadlines", facts=[{"text": "f1", "lang": "en"}])
    assert out["remembered"] == {"ok": True}
    assert brain.remembered[0]["text"] == "vizhi likes deadlines"
    assert brain.remembered[1] == {"text": "f1", "user": "tester", "lang": "en"}
    with pytest.raises(ValueError):
        mem.store()
    with pytest.raises(ValueError):
        mem.store(facts=[{"lang": "en"}])


def test_executor_requires_harness():
    with pytest.raises(ValueError):
        HarnessExecutor()


def test_inspect_verdicts(harness_vizhi, harness_arivu):
    vizhi = HarnessExecutor(harness_vizhi)
    assert vizhi.inspect("file", {"action": "read", "path": "n.md"},
                         _ctx()) == Verdict.ALLOW
    assert vizhi.inspect("file", {"action": "write", "path": "n.md"},
                         _ctx()) == Verdict.ASK
    assert vizhi.inspect("nope", {}, _ctx()) == Verdict.DENY
    assert vizhi.inspect("file", {"action": "read", "path": "n.md"},
                         _ctx(worker="ghost")) == Verdict.DENY
    assert vizhi.inspect("file", {"action": "read", "path": "../escape.md"},
                         _ctx()) == Verdict.DENY
    arivu = HarnessExecutor(harness_arivu)
    assert arivu.inspect("terminal", {"action": "exec", "command": "ls"},
                         _ctx(worker="aayan")) == Verdict.DENY


def test_run_read_roundtrip(harness_vizhi):
    Path(harness_vizhi.session_dir, "notes.md").write_text("hello world",
                                                           encoding="utf-8")
    out = HarnessExecutor(harness_vizhi).run(
        "file", {"action": "read", "path": "notes.md"}, _ctx())
    assert out.ok is True
    assert "hello world" in str(out.data)


def test_run_ask_paths(harness_vizhi):
    executor = HarnessExecutor(harness_vizhi)
    asked = executor.run("file", {"action": "write", "path": "o.md",
                                  "content": "x", "mode": "create"}, _ctx())
    assert asked.ok is False
    assert asked.data.get("need_approval") is True
    # Valid approval -> executes through the authority-bound approver.
    done = executor.run("file", {"action": "write", "path": "o.md",
                                 "content": "x", "mode": "create"},
                        _ctx(approval=_approval(), valid=True))
    assert done.ok is True
    assert Path(harness_vizhi.session_dir, "o.md").read_text(
        encoding="utf-8") == "x"
    # Present-but-invalid approval -> revoked, never silent.
    with pytest.raises(ApprovalRevoked):
        executor.run("file", {"action": "write", "path": "p.md",
                              "content": "x", "mode": "create"},
                     _ctx(approval=_approval(), valid=False))
    # Approval state is irrelevant for tools that need none.
    allowed = executor.run("file", {"action": "list", "path": "."},
                           _ctx(approval=_approval(), valid=False))
    assert allowed.ok is True


def test_run_cancelled_and_garbage(harness_vizhi):
    executor = HarnessExecutor(harness_vizhi)
    with pytest.raises(TaskCancelled):
        executor.run("file", {"action": "read", "path": "n.md"},
                     _ctx(cancelled=True))

    class _Stub:
        def inspect(self, name, args=None, allow=None, worker=None,
                    approver=None):
            return {"ok": True}

        def run(self, name, args=None, allow=None, worker=None,
                approver=None):
            return "not json at all"

    out = HarnessExecutor(_Stub()).run("file", {}, _ctx())
    assert out.ok is False and out.error == "non-JSON tool output"


def test_make_factories(tmp_workspace):
    from nak_host.wiring import make_executor, make_memory

    brain = _Brain()
    assert make_memory(brain=brain, user="t").recall("q")["count"] == 2
    # Workspace must live under the repo workspace/ (resource globs deny
    # anything outside it — same rule as the conftest fixtures).
    executor = make_executor(worker="vizhi", workspace_root=tmp_workspace)
    assert executor.inspect("file", {"action": "read", "path": "n.md"},
                            _ctx()) == Verdict.ALLOW
