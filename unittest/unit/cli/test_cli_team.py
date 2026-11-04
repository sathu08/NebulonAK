"""CLI --team is a deprecated alias for --craft (team is part of craft).

Drives nak.worker.__main__.main() end-to-end offline by patching the
composition root's make_brain/make_worker/make_harness.
"""
import json

from nak.worker import __main__ as cli


class PersistentBrain:
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


def _patch(monkeypatch, brain, workspace):
    """Build offline workers + a real harness rooted in tmp_workspace."""
    import nak.wiring as wiring
    from nak.worker import GenericWorker

    real_make_harness = wiring.make_harness

    monkeypatch.setattr(wiring, "make_brain", lambda **k: brain)
    monkeypatch.setattr(
        wiring, "make_worker",
        lambda name, brain=None, **kw: GenericWorker(name, brain=brain))
    monkeypatch.setattr(
        wiring, "make_harness",
        lambda **kw: real_make_harness(workspace_root=workspace, **kw))


def _run(capsys, argv):
    rc = cli.main(argv)
    out = capsys.readouterr().out
    return rc, json.loads(out)


def test_cli_team_alias_dispatches_as_craft(monkeypatch, capsys, tmp_workspace):
    brain = PersistentBrain(answer="Plan:\n1. dig into logs [worker=aayan]")
    _patch(monkeypatch, brain, tmp_workspace)
    rc, out = _run(capsys, ["--worker", "vizhi", "--team", "investigate outage",
                            "--approve", "--json"])
    assert rc == 0
    assert out["planned"] is True
    assert out["dispatched"] is True
    assert [r["worker"] for r in out["runs"]] == ["aayan"]
    assert out["session_id"] in brain.docs


def test_cli_team_mints_a_session(monkeypatch, capsys, tmp_workspace):
    brain = PersistentBrain(answer="Plan:\n1. plan [worker=aayan]")
    _patch(monkeypatch, brain, tmp_workspace)
    rc, out = _run(capsys, ["--worker", "vizhi", "--team", "investigate",
                            "--approve", "--json"])
    assert rc == 0
    assert out["session_id"].startswith("user_")
    assert out["session_id"] in brain.docs


def test_cli_team_skips_unknown_worker(monkeypatch, capsys, tmp_workspace):
    brain = PersistentBrain(answer="Plan:\n1. x [worker=ghost]")
    _patch(monkeypatch, brain, tmp_workspace)
    rc, out = _run(capsys, ["--worker", "vizhi", "--team", "go",
                            "--approve", "--json"])
    assert rc == 0
    assert out["assigned_workers"] == []
    assert out["runs"] == []
    assert out["dispatched"] is False


def test_cli_team_max_workers_keeps_all(monkeypatch, capsys, tmp_workspace):
    brain = PersistentBrain(
        answer="Plan:\n1. a [worker=aayan]\n2. b [worker=arivu]")
    _patch(monkeypatch, brain, tmp_workspace)
    rc, out = _run(capsys, ["--worker", "vizhi", "--team", "go",
                            "--approve", "--max-workers", "1", "--json"])
    assert rc == 0
    assert sorted(r["worker"] for r in out["runs"]) == ["aayan", "arivu"]


def test_cli_team_name_uses_leader_and_no_parallel(monkeypatch, capsys,
                                                   tmp_workspace):
    brain = PersistentBrain(answer="Plan:\n1. dig into logs [worker=aayan]")
    _patch(monkeypatch, brain, tmp_workspace)
    rc, out = _run(capsys, ["--team-name", "core", "--craft", "investigate",
                            "--approve", "--no-parallel", "--json"])
    assert rc == 0
    assert out["planner"] == "vizhi"  # core.leader, --worker omitted
    assert out["team"] == "core"
    assert out["parallel"] is False
    assert out["dispatched"] is True
    assert [r["worker"] for r in out["runs"]] == ["aayan"]


def test_cli_unknown_team_name_fails(monkeypatch, capsys, tmp_workspace):
    brain = PersistentBrain(answer="hi")
    _patch(monkeypatch, brain, tmp_workspace)
    rc = cli.main(["--team-name", "ghost", "--craft", "go", "--json"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "team error" in err


def test_cli_session_report_still_works(monkeypatch, capsys):
    brain = PersistentBrain(answer="hi")
    _patch(monkeypatch, brain, "workspace")
    rc, out = _run(capsys, ["--worker", "vizhi", "--session", "s1", "--json"])
    assert rc == 0
    assert out["session_id"] == "s1"
    assert "turns" in out
