"""Team policy: planner roster rules and member-restricted dispatch."""
import json
import shutil
from pathlib import Path

import pytest

from nak_host.worker import GenericWorker
from nak_host.worker.engine.team import resolve_team
from nak_host.wiring import make_harness, run_craft

_REAL_CRAFTS = Path(__file__).resolve().parents[3] / "nak_host" / "manifests" / "crafts.json"


class _Brain:
    def __init__(self, answer="ack"):
        self.answer = answer
        self.docs = {}

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
        return {"answer": self.answer}


def _worker(name, team=None, teams=None, crafts=("plan", "assign")):
    e = {
        "name": name,
        "type": "coordinator",
        "identity": {"role": "tester"},
        "memory": {"enabled": True},
        "crafts": list(crafts),
    }
    if team is not None:
        e["team"] = team
    if teams is not None:
        e["teams"] = teams
    return e


def _team(name, members, **extra):
    t = {
        "name": name,
        "leader": members[0],
        "members": members,
        "shared_memory": True,
    }
    t.update(extra)
    return t


def _write(tmp_path, workers, teams=None):
    shutil.copy(_REAL_CRAFTS, tmp_path / "crafts.json")
    wf = tmp_path / "workers.json"
    wf.write_text(json.dumps(workers), encoding="utf-8")
    if teams is not None:
        (tmp_path / "teams.json").write_text(
            json.dumps({"teams": teams}), encoding="utf-8")
    return str(wf)


def _run(tmp_path, workers, teams, answer, team=None):
    wf = _write(tmp_path, workers, teams)
    brain = _Brain(answer)
    vizhi = GenericWorker("vizhi", brain=brain, workers_file=wf)
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)
    res = run_craft(
        "ship it", vizhi, h, approver=lambda info: True, team=team,
        factory=lambda n: GenericWorker(n, brain=brain, workers_file=wf),
    )
    return res


def test_dispatch_is_restricted_to_team_members(tmp_path):
    res = _run(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"}),
         _worker("arivu")],
        [_team("core", ["vizhi", "aayan"], max_workers=2)],
        "Plan:\n1. do x [worker=aayan]\n2. do y [worker=arivu]",
        team="core",
    )
    assert res["planned"] is True
    assert res["dispatched"] is True
    assert res["team"] == "core"
    assert res["members"] == ["vizhi", "aayan"]
    assert res["planner"] == "vizhi"
    assert res["planner_outside_team"] is False
    assert res["workers"] == ["aayan"]
    assert [r["worker"] for r in res["runs"]] == ["aayan"]
    assert res["blocked"] == ["arivu"]


def test_planner_may_be_outside_team(tmp_path):
    res = _run(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "research"})],
        [_team("core", ["vizhi"]), _team("research", ["aayan"])],
        "Plan:\n1. research x [worker=aayan]",
        team="research",
    )
    assert res["planned"] is True
    assert res["dispatched"] is True
    assert res["planner"] == "vizhi"
    assert res["planner_outside_team"] is True
    assert res["workers"] == ["aayan"]


def test_strict_team_rejects_outside_planner(tmp_path):
    # vizhi's default team is core; planning for research is rejected.
    res = _run(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "research"})],
        [_team("core", ["vizhi"]),
         _team("research", ["aayan"], planner_must_be_member=True)],
        "Plan:\n1. research x [worker=aayan]",
        team="research",
    )
    assert res["planned"] is False
    assert "must be a member" in res["reason"]


def test_no_team_allows_any_worker(tmp_path):
    res = _run(
        tmp_path,
        [_worker("vizhi"), _worker("aayan")],
        None,
        "Plan:\n1. do x [worker=aayan]",
    )
    assert res["planned"] is True
    assert res["dispatched"] is True
    assert res["team"] == ""
    assert res["members"] == []
    assert res["planner_outside_team"] is False
    assert res["workers"] == ["aayan"]
    assert res["blocked"] == []


def test_multi_team_worker_needs_explicit_team(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", teams=["core", "research"])],
        [_team("core", ["vizhi"]), _team("research", ["vizhi"])],
    )
    vizhi = GenericWorker("vizhi", workers_file=wf)
    with pytest.raises(ValueError, match="multiple teams"):
        resolve_team(vizhi)
    assert resolve_team(vizhi, "research")["name"] == "research"
