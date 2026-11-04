import json

import pipeline.vetting.cli.improve as cli


def test_improve_plan_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "plan_improvements",
        lambda *args, **kwargs: {"run_dir": "run", "actions": []},
    )
    code = cli.main(["plan", "run"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["run_dir"] == "run"


def test_improve_apply_reports_status(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "apply_improvements",
        lambda *args, **kwargs: {
            "status": "staged",
            "candidate_root": "cand",
            "run_dir": "run",
        },
    )
    code = cli.main(["apply", "run", "--no-core", "--no-verify"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["status"] == "staged"


def test_improve_apply_rejects_bad_threshold(capsys):
    code = cli.main(["apply", "run", "--threshold", "2"])
    assert code == 2
    assert "threshold" in capsys.readouterr().err


def test_improve_discard(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discard_candidate", lambda *args, **kwargs: True)
    code = cli.main(["discard", "run"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["discarded"] is True
