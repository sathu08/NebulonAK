"""CLI --craft: plan -> nak_plan.md -> approval, all through nak_host.wiring.run_craft."""
import json

from conftest import FakeBrain
from nak_host.worker import __main__ as cli


def _patch(monkeypatch, brain, workspace):
    """Build offline workers + a real harness rooted in tmp_workspace."""
    import nak_host.wiring as wiring
    from nak_host.worker import GenericWorker

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


def test_cli_craft_plans_and_pends(monkeypatch, capsys, tmp_workspace):
    brain = FakeBrain(answer="1. read notes\n2. summarize")
    _patch(monkeypatch, brain, tmp_workspace)
    rc, out = _run(capsys, ["--worker", "vizhi", "--craft", "summarize notes",
                            "--json"])
    assert rc == 0
    assert out["planned"] is True
    assert out["needs_approval"] is True
    assert out["approved"] is False
    assert out["steps"] == ["read notes", "summarize"]
    assert out["path"].endswith("nak_plan.md")


def test_cli_craft_approve_flag(monkeypatch, capsys, tmp_workspace):
    brain = FakeBrain(answer="1. read notes\n2. summarize")
    _patch(monkeypatch, brain, tmp_workspace)
    rc, out = _run(capsys, ["--worker", "vizhi", "--craft", "summarize notes",
                            "--approve", "--json"])
    assert rc == 0
    assert out["planned"] is True
    assert out["needs_approval"] is True
    assert out["approved"] is True


def test_cli_craft_auto_approved_small(monkeypatch, capsys, tmp_workspace):
    brain = FakeBrain(answer="1. read notes\n2. summarize")
    _patch(monkeypatch, brain, tmp_workspace)
    rc, out = _run(capsys, ["--worker", "aayan", "--craft", "summarize notes",
                            "--json"])
    assert rc == 0
    assert out["planned"] is True
    assert out["needs_approval"] is False
    assert out["approved"] is True
