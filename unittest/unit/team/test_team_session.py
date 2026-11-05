"""Craft team dispatch: peer workers share ONE session transcript.

There is no team object and no sub-worker hierarchy. run_craft plans, then
each [worker=name] runs as a peer in its own thread with its own brain,
while every turn merges into the same session doc.
"""
import re
import threading
from datetime import datetime, timezone

import pytest

from nak_host.worker import GenericWorker
from nak_host.worker.engine.dispatch import run_workers
from nak_host.wiring import new_session, run_craft


class TeamBrain:
    """Duck-typed brain whose chat docs persist (save_chat -> get_chat)."""

    def __init__(self, answer="ack"):
        self.answer = answer
        self.docs = {}
        self.chat_calls = []

    def require_mind(self):
        pass

    def search(self, text, top_k=3):
        return []

    def decide(self, turns):
        return {"decisions": []}

    def remember(self, text, **kwargs):
        return {"ok": True}

    def save_chat(self, doc):
        self.docs[doc["id"]] = [dict(m) for m in doc.get("messages", [])]
        return {"ok": True, "id": doc["id"]}

    def get_chat(self, chat_id):
        if chat_id not in self.docs:
            raise ValueError(f"unknown chat {chat_id!r}")
        return {"chat": {"id": chat_id, "messages": list(self.docs[chat_id])}}

    def chat(self, prompt, messages=None, session_id=None):
        self.chat_calls.append({"prompt": prompt, "messages": messages,
                                "session_id": session_id})
        return {"answer": self.answer}


# ---------- P1: one collision-safe shared key ----------

def test_new_session_ids_are_unique_and_filename_safe():
    when = datetime(2026, 10, 19, tzinfo=timezone.utc)
    a = new_session("team", when=when)
    b = new_session("team", when=when)
    assert a != b  # same user + same second -> still distinct
    assert a.startswith("team_20261019T000000__")
    assert re.fullmatch(r"[A-Za-z0-9_]+", a)


def test_run_craft_reports_shared_session_id(worker_vizhi, tmp_workspace):
    from nak_host.wiring import make_harness

    worker_vizhi.brain.answer = "1. read notes\n2. summarize"
    h = make_harness(worker="vizhi", workspace_root=tmp_workspace)
    res = run_craft("summarize notes", worker_vizhi, h, approver=lambda i: True)
    assert res["planned"] is True
    assert res["session_id"] == h.session_id
    assert res["dispatched"] is False


# ---------- peer worker dispatch ----------

def test_run_workers_shares_one_transcript():
    brain = TeamBrain(answer="ack")
    coordinator = GenericWorker("vizhi", brain=brain)
    arivu = GenericWorker("arivu", brain=brain)
    sid = new_session("team")
    coordinator.chat("ship the login fix", session_id=sid)
    out = run_workers("Plan:\n1. review login [worker=arivu]",
                      ["arivu"], goal="ship the login fix",
                      workers={"arivu": arivu}, session_id=sid)
    assert [r["worker"] for r in out["runs"]] == ["arivu"]
    assert out["runs"][0]["memory"]["shared"] is True
    users = [m["content"] for m in brain.docs[sid] if m["role"] == "user"]
    assert "ship the login fix" in users
    assert any("review login" in u for u in users)
    assert "[worker=" not in " ".join(users)  # assignment tag stripped


def test_run_workers_delivers_planner_turn():
    brain = TeamBrain(answer="ack")
    coordinator = GenericWorker("vizhi", brain=brain)
    arivu = GenericWorker("arivu", brain=brain)
    coordinator.chat("codeword TANGERINE", session_id="team")
    run_workers("Plan:\n1. hello [worker=arivu]", ["arivu"],
                goal="codeword TANGERINE", workers={"arivu": arivu},
                session_id="team")
    # arivu's injected prompt carried the planner's shared turn
    block = next(m["content"] for m in brain.chat_calls[-1]["messages"]
                 if "session team so far" in m["content"])
    assert "TANGERINE" in block


def test_run_workers_skips_planner_and_unknown():
    brain = TeamBrain(answer="ack")
    out = run_workers("Plan:\n1. x [worker=vizhi]\n2. y [worker=ghost]",
                      ["vizhi", "ghost"], goal="g",
                      factory=lambda n: None, session_id="s",
                      exclude="vizhi")
    assert out["workers"] == ["ghost"]
    assert out["skipped"] == ["vizhi"]
    assert out["runs"][0] == {"worker": "ghost", "ok": False,
                              "reason": "unknown worker"}


def test_run_workers_does_not_drop_when_max_workers_is_one():
    brain = TeamBrain(answer="ack")
    built = {}

    def factory(name):
        built.setdefault(name, GenericWorker(name, brain=brain))
        return built[name]

    out = run_workers("Plan:\n1. a [worker=arivu]\n2. b [worker=aayan]",
                      ["arivu", "aayan"], goal="g", factory=factory,
                      session_id="s", max_workers=1)
    assert [r["worker"] for r in out["runs"]] == ["arivu", "aayan"]
    assert all(r["ok"] for r in out["runs"])


def test_run_craft_dispatches_assigned_workers(tmp_workspace):
    from nak_host.wiring import make_harness

    brain = TeamBrain(answer="Plan:\n1. review login [worker=arivu]")
    coordinator = GenericWorker("vizhi", brain=brain)
    h = make_harness(worker="vizhi", workspace_root=tmp_workspace)
    res = run_craft("ship the login fix", coordinator, h,
                    approver=lambda i: True,
                    factory=lambda n: GenericWorker(n, brain=brain))
    assert res["planned"] is True
    assert res["dispatched"] is True
    assert [r["worker"] for r in res["runs"]] == ["arivu"]
    assert res["session_id"] == h.session_id
    # coordinator planned once; only the assigned peer chatted after it
    assert len(brain.chat_calls) == 2


def test_run_craft_pending_does_not_dispatch(tmp_workspace):
    from nak_host.wiring import make_harness

    brain = TeamBrain(answer="Plan:\n1. review login [worker=arivu]")
    coordinator = GenericWorker("vizhi", brain=brain)
    h = make_harness(worker="vizhi", workspace_root=tmp_workspace)
    res = run_craft("ship the login fix", coordinator, h, approver=None,
                    factory=lambda n: GenericWorker(n, brain=brain))
    assert res["planned"] is True
    assert res["needs_approval"] is True
    assert res["dispatched"] is False
    assert res["runs"] == []


def test_run_craft_plan_only_worker_does_not_dispatch(tmp_workspace):
    from nak_host.wiring import make_harness

    brain = TeamBrain(answer="Plan:\n1. review login [worker=arivu]")
    coordinator = GenericWorker("aayan", brain=brain)
    h = make_harness(worker="aayan", workspace_root=tmp_workspace)
    res = run_craft("summarize notes", coordinator, h,
                    approver=lambda i: True,
                    factory=lambda n: GenericWorker(n, brain=brain))
    assert res["planned"] is True
    assert res["assigned_workers"] == []
    assert res["dispatched"] is False


def test_run_craft_uses_one_brain_and_thread_per_peer(monkeypatch,
                                                      tmp_workspace):
    from nak_host.wiring import make_harness

    seen = []
    docs = {}
    barrier = threading.Barrier(2, timeout=5)

    class PeerBrain(TeamBrain):
        def __init__(self, answer):
            super().__init__(answer=answer)
            self.docs = docs

        def chat(self, prompt, messages=None, session_id=None):
            seen.append((threading.get_ident(), id(self), str(prompt)))
            if str(prompt) != "go":
                try:
                    barrier.wait()  # prove the peers really run in parallel
                except threading.BrokenBarrierError:
                    pass
            return super().chat(prompt, messages=messages,
                                session_id=session_id)

    plan_brain = PeerBrain("Plan:\n1. a [worker=aayan]\n2. b [worker=arivu]")

    import nak_host.wiring as wiring

    monkeypatch.setattr(wiring, "make_brain", lambda **k: PeerBrain("ack"))
    monkeypatch.setattr(
        wiring, "make_worker",
        lambda name, brain=None, **kw: GenericWorker(name, brain=brain))

    coordinator = GenericWorker("vizhi", brain=plan_brain)
    h = make_harness(worker="vizhi", workspace_root=tmp_workspace)
    res = run_craft("go", coordinator, h, approver=lambda i: True)
    assert res["dispatched"] is True
    assert sorted(r["worker"] for r in res["runs"]) == ["aayan", "arivu"]
    peers = [x for x in seen if x[2] != "go"]
    assert len(peers) == 2
    assert len({x[1] for x in peers}) == 2  # one brain per peer
    assert len({x[0] for x in peers}) == 2  # one thread per peer
