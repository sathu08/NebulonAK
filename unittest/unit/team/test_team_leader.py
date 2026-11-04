"""Section 4: team leader as default planner + parallel/shared team memory."""
import json
import shutil
from pathlib import Path

import pytest

from nak.worker import GenericWorker
from nak.worker.engine.dispatch import run_workers
from nak.wiring import make_harness, run_craft

_REAL_CRAFTS = Path(__file__).resolve().parents[3] / "nak" / "manifests" / "crafts.json"


class _Brain:
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


def _worker(name, team=None, crafts=("plan", "assign")):
    e = {"name": name, "type": "coordinator",
         "identity": {"role": "tester"},
         "memory": {"enabled": True}, "crafts": list(crafts)}
    if team is not None:
        e["team"] = team
    return e


def _team(name, members, **extra):
    t = {"name": name, "leader": members[0], "members": members,
         "shared_memory": True}
    t.update(extra)
    return t


def _write(tmp_path, workers, teams):
    shutil.copy(_REAL_CRAFTS, tmp_path / "crafts.json")
    wf = tmp_path / "workers.json"
    wf.write_text(json.dumps(workers), encoding="utf-8")
    (tmp_path / "teams.json").write_text(
        json.dumps({"teams": teams}), encoding="utf-8")
    return str(wf)


# ---------- section 4: leader becomes the planner ----------

def test_harness_as_second_positional_uses_team_leader(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"})],
        [_team("core", ["vizhi", "aayan"])],
    )
    brain = _Brain("Plan:\n1. do x [worker=aayan]")
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)
    res = run_craft(
        "ship it", h, team="core", approver=lambda i: True,
        factory=lambda n: GenericWorker(n, brain=brain, workers_file=wf),
    )
    assert res["planned"] is True
    assert res["planner"] == "vizhi"  # team.leader
    assert res["team"] == "core"
    assert res["dispatched"] is True
    assert res["workers"] == ["aayan"]


def test_dispatch_false_plans_without_peer_runs(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"})],
        [_team("core", ["vizhi", "aayan"])],
    )
    brain = _Brain("Plan:\n1. do x [worker=aayan]")
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)
    res = run_craft(
        "ship it", h, team="core", approver=lambda i: True,
        factory=lambda n: GenericWorker(n, brain=brain, workers_file=wf),
        dispatch=False,
    )
    assert res["planned"] is True
    assert res["approved"] is True
    assert res["dispatched"] is False
    assert res["workers"] == []
    assert res["runs"] == []


def test_omitting_worker_and_team_raises(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"})],
        [_team("core", ["vizhi", "aayan"])],
    )
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)
    with pytest.raises(ValueError, match="no planner"):
        run_craft("ship it", harness=h)


def test_unknown_team_returns_error_not_raise(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"})],
        [_team("core", ["vizhi", "aayan"])],
    )
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)
    res = run_craft("ship it", h, team="ghost")
    assert res["planned"] is False
    assert "team error" in res["reason"]


# ---------- parallel ----------

def test_run_craft_parallel_false_reports_and_runs_serially(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"}),
         _worker("arivu", {"enabled": True, "name": "core"})],
        [_team("core", ["vizhi", "aayan", "arivu"])],
    )
    brain = _Brain("Plan:\n1. a [worker=aayan]\n2. b [worker=arivu]")
    vizhi = GenericWorker("vizhi", brain=brain, workers_file=wf)
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)
    res = run_craft(
        "ship it", vizhi, h, team="core", approver=lambda i: True,
        parallel=False,
        factory=lambda n: GenericWorker(n, brain=brain, workers_file=wf),
    )
    assert res["parallel"] is False
    assert [r["worker"] for r in res["runs"]] == ["aayan", "arivu"]


def test_team_parallel_policy_is_used_unless_overridden(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"}),
         _worker("arivu", {"enabled": True, "name": "core"})],
        [_team("core", ["vizhi", "aayan", "arivu"], parallel=False)],
    )
    brain = _Brain("Plan:\n1. a [worker=aayan]\n2. b [worker=arivu]")
    vizhi = GenericWorker("vizhi", brain=brain, workers_file=wf)
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)

    def _run(**kw):
        return run_craft(
            "ship it", vizhi, h, team="core", approver=lambda i: True,
            factory=lambda n: GenericWorker(n, brain=brain, workers_file=wf),
            **kw,
        )

    assert _run()["parallel"] is False          # team policy
    assert _run(parallel=True)["parallel"] is True  # explicit wins


def test_run_workers_parallel_flag_defaults_true(tmp_path):
    brain = _Brain()
    out = run_workers(
        "Plan:\n1. a [worker=aayan]", ["aayan"], goal="g", session_id="s",
        factory=lambda n: GenericWorker(n, brain=brain), parallel=False)
    assert out["parallel"] is False


# ---------- shared team memory + leader access ----------

def test_team_memory_is_readable_by_leader(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"})],
        [_team("core", ["vizhi", "aayan"])],
    )
    brain = _Brain("Plan:\n1. dig into logs [worker=aayan]")
    vizhi = GenericWorker("vizhi", brain=brain, workers_file=wf)
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)
    res = run_craft(
        "ship it", vizhi, h, team="core", approver=lambda i: True,
        factory=lambda n: GenericWorker(n, brain=brain, workers_file=wf),
    )
    assert res["shared_memory"] is True
    mem = res["team_memory"]
    assert mem["session_id"] == h.session_id
    users = [t["preview"] for t in mem["turns"] if t["role"] == "user"]
    assert any("dig into logs" in u for u in users)  # peer turn is visible
    assert vizhi.team_memory(h.session_id)["message_count"] == mem["message_count"]


def test_shared_memory_false_keeps_peer_turns_out(tmp_path):
    wf = _write(
        tmp_path,
        [_worker("vizhi", {"enabled": True, "name": "core"}),
         _worker("aayan", {"enabled": True, "name": "core"})],
        [_team("core", ["vizhi", "aayan"], shared_memory=False)],
    )
    brain = _Brain("Plan:\n1. dig into logs [worker=aayan]")
    vizhi = GenericWorker("vizhi", brain=brain, workers_file=wf)
    h = make_harness(worker="vizhi", workspace_root=tmp_path, workers_file=wf)
    res = run_craft(
        "ship it", vizhi, h, team="core", approver=lambda i: True,
        factory=lambda n: GenericWorker(n, brain=brain, workers_file=wf),
    )
    assert res["shared_memory"] is False
    assert "team_memory" not in res
    # peer did not append its directive to the planner's session doc
    users = [m["content"] for m in brain.docs[h.session_id]
             if m["role"] == "user"]
    assert not any("dig into logs" in u for u in users)
