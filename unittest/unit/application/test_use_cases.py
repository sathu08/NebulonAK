"""Application use-case tests (fake planner/harness, real memory store)."""

from __future__ import annotations

import re

import pytest

from nak.adapters.store_memory import InMemoryTaskStore
from nak.application import assign_task, create_task, plan_task
from nak.domain import TaskState
from nak.worker.engine.team import split_assignments

_ASSIGN_RE = re.compile(r"\[\s*worker\s*=\s*([^\]\s]+)\s*\]", re.IGNORECASE)


class FakePlanner:
    def __init__(self, answer, triage=None):
        self._answer = answer
        self._triage = triage

    def chat(self, text, session_id=None):
        out = {"answer": self._answer}
        if self._triage is not None:
            out["triage"] = self._triage
        return out

    @staticmethod
    def extract_plan(answer):
        steps = []
        for line in str(answer or "").splitlines():
            match = re.match(r"^\s*(\d+)[.)]\s+(\S.*)$", line)
            if match:
                steps.append(match.group(2).strip())
        return steps

    @staticmethod
    def extract_assigned(answer, known=None):
        names = [m.group(1).strip()
                 for m in _ASSIGN_RE.finditer(str(answer or ""))]
        seen = []
        for name in names:
            if name and name not in seen and (known is None or name in known):
                seen.append(name)
        return seen

    @staticmethod
    def needs_plan_approval(n_steps):
        return int(n_steps) > 2


class FakeHarness:
    session_id = "sess-1"
    session_dir = "/tmp/ws"

    def __init__(self):
        self.plans = []
        self.approvals = []

    def start_plan(self, goal, steps, assigned_workers=None, needs_approval=False,
                   decision_evidence=None):
        self.plans.append({"goal": goal, "steps": list(steps)})
        return {"path": "/tmp/ws/nak_plan.md", "steps": list(steps)}

    def approve_plan(self, approved, by=None, note=None):
        self.approvals.append(bool(approved))


def _planned_task(store, answer="1. one [worker=arivu]\n2. two [worker=ezhilan]"):
    task, run = create_task("do it", store=store, workspace="/tmp/ws")
    planner = FakePlanner(answer)
    harness = FakeHarness()
    out = plan_task(task, run, store=store, planner=planner, harness=harness,
                    approver=lambda info: True,
                    team_spec={"name": "core", "members": ["vizhi", "arivu", "ezhilan"]},
                    candidates=["arivu", "ezhilan"],
                    known_workers=["vizhi", "arivu", "ezhilan"])
    return task, out


def test_create_dedupes_by_idempotency_key():
    store = InMemoryTaskStore()
    first, _ = create_task("do it", store=store, workspace="/tmp/ws",
                           idempotency_key="same")
    second, _ = create_task("do it", store=store, workspace="/tmp/ws",
                            idempotency_key="same")
    assert first.id == second.id
    with pytest.raises(ValueError):
        create_task("   ", store=store, workspace="/tmp/ws")


def test_plan_auto_approved_small_plan():
    store = InMemoryTaskStore()
    task, run = create_task("do it", store=store, workspace="/tmp/ws")
    out = plan_task(task, run, store=store, planner=FakePlanner("1. one\n2. two"),
                    harness=FakeHarness())
    assert out["planned"] and out["approved"] and not out["needs_approval"]
    assert store.get_task(task.id)[1].state == TaskState.APPROVED


def test_plan_approval_gate():
    store = InMemoryTaskStore()
    task, run = create_task("do it", store=store, workspace="/tmp/ws")
    planner = FakePlanner("1. one\n2. two\n3. three")
    out = plan_task(task, run, store=store, planner=planner,
                    harness=FakeHarness(), approver=lambda info: False)
    assert out["planned"] and not out["approved"] and out["needs_approval"]
    assert store.get_task(task.id)[1].state == TaskState.NEEDS_APPROVAL


def test_plan_chat_failure_marks_failed():
    store = InMemoryTaskStore()
    task, run = create_task("do it", store=store, workspace="/tmp/ws")

    class Broken(FakePlanner):
        def chat(self, text, session_id=None):
            raise RuntimeError("brain down")

    out = plan_task(task, run, store=store, planner=Broken(""),
                    harness=FakeHarness())
    assert not out["planned"] and "brain down" in out["reason"]
    assert store.get_task(task.id)[1].state == TaskState.FAILED


def test_plan_no_steps_and_clarify():
    store = InMemoryTaskStore()
    task, run = create_task("do it", store=store, workspace="/tmp/ws")
    out = plan_task(task, run, store=store, planner=FakePlanner("just words"),
                    harness=FakeHarness())
    assert not out["planned"] and out["reason"] == "no numbered steps in reply"
    assert store.get_task(task.id)[1].state == TaskState.PLANNED

    task2, run2 = create_task("do that", store=store, workspace="/tmp/ws")
    out2 = plan_task(task2, run2, store=store,
                     planner=FakePlanner("1. one",
                                         {"policy": {"action": "clarify"}}),
                     harness=FakeHarness())
    assert not out2["planned"] and "clarify" in out2["reason"]


def test_plan_decision_error_forces_approval():
    store = InMemoryTaskStore()
    task, run = create_task("do it", store=store, workspace="/tmp/ws")

    def boom(answer, candidates):
        raise RuntimeError("router down")

    out = plan_task(task, run, store=store, planner=FakePlanner("1. one"),
                    harness=FakeHarness(), route_steps=boom)
    assert out["planned"] and out["needs_approval"] and not out["approved"]
    assert out["decision"]["status"] == "error"


def test_assign_records_split_and_transitions():
    store = InMemoryTaskStore()
    task, out = _planned_task(store)
    run = out.pop("run")
    assert out["approved"]
    result = assign_task(task, run, store=store,
                         team_spec={"name": "core",
                                    "members": ["vizhi", "arivu", "ezhilan"]},
                         assigned=out["assigned_workers"],
                         planner_name="vizhi", splitter=split_assignments)
    assert result["allowed"] == ["arivu", "ezhilan"]
    assert result["blocked"] == []
    assert store.get_task(task.id)[1].state == TaskState.ASSIGNED


def test_assign_without_approval_reports_but_does_not_transition():
    store = InMemoryTaskStore()
    task, run = create_task("do it", store=store, workspace="/tmp/ws")
    planner = FakePlanner("1. one [worker=ghost]\n2. two [worker=arivu]\n3. three")
    out = plan_task(task, run, store=store, planner=planner,
                    harness=FakeHarness(), approver=lambda info: False,
                    team_spec={"name": "core", "members": ["vizhi", "arivu"]},
                    known_workers=["vizhi", "arivu", "ghost"])
    run = out.pop("run")
    result = assign_task(task, run, store=store,
                         team_spec={"name": "core", "members": ["vizhi", "arivu"]},
                         assigned=out["assigned_workers"],
                         planner_name="vizhi", splitter=split_assignments)
    assert result["blocked"] == ["ghost"]
    assert store.get_task(task.id)[1].state == TaskState.NEEDS_APPROVAL
