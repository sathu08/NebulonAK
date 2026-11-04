import json

from pipeline.vetting.improve import (
    apply_improvements,
    collect_improvements,
    plan_improvements,
)
from pipeline.vetting.utils.models import (
    Answer,
    Case,
    Improvement,
    Judgement,
    WorkerResult,
)
from pipeline.vetting.report import render_worker_md
from pipeline.vetting.utils.runner import _build_improvements


def _summary(tmp_path, craft_workers=("vizhi",)):
    run = tmp_path / "run"
    run.mkdir()
    summary = {
        "run_dir": str(run),
        "workers": [{
            "name": "vizhi",
            "improvements": [{
                "id": "vizhi-1",
                "worker": "vizhi",
                "source": "craft",
                "scope": "craft",
                "severity": "high",
                "target": "plan",
                "problem": "Craft plan probe error.",
                "evidence": "missing craft.md",
                "suggestion": "Fix craft plan.",
                "affected_workers": list(craft_workers),
                "status": "open",
            }],
        }],
    }
    (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run


def _craft_root(tmp_path):
    root = tmp_path / "craft"
    target = root / "plan"
    target.mkdir(parents=True)
    (target / "craft.md").write_text("# plan\n\nDo the plan.\n", encoding="utf-8")
    return root


def test_build_improvements_covers_cases_checks_and_crafts():
    case = Case(id="q1", question="Q?", expected="the answer",
                rubric="R", difficulty="easy")
    result = WorkerResult(
        name="vizhi", threshold=0.6, status="fail",
        cases=[case],
        answers=[Answer(case_id="q1", text="nope")],
        judgements=[Judgement(case_id="q1", score=0.1, evidence="off topic")],
        checks=[{"case_id": "q1", "id": "c1", "op": "contains",
                 "expected": "answer", "actual": "nope", "passed": False}],
    )
    result.craft_probes = [type("P", (), {
        "name": "plan", "status": "error", "detail": "missing"})()]
    out = _build_improvements(result)
    sources = {item.source for item in out}
    assert {"case", "check", "craft"} <= sources
    craft = next(item for item in out if item.source == "craft")
    assert craft.scope == "craft"
    assert craft.target == "plan"
    assert "nunnan" not in craft.affected_workers
    assert all(item.id.startswith("vizhi-") for item in out)


def test_report_lists_where_to_improve():
    result = WorkerResult(name="vizhi")
    result.improvements = [Improvement(
        id="vizhi-1", worker="vizhi", source="craft", scope="craft",
        target="plan", problem="Craft plan failed.",
        evidence="missing", suggestion="Fix it.")]
    md = render_worker_md(result)
    assert "## Where to improve" in md
    assert "Craft plan failed." in md
    assert "Fix it." in md


def test_collect_improvements_deduplicates():
    item = {"worker": "vizhi", "source": "case", "target": "q1",
            "suggestion": "fix"}
    summary = {"workers": [
        {"name": "vizhi", "improvements": [item]},
        {"name": "vizhi", "improvements": [dict(item)]},
    ]}
    assert len(collect_improvements(summary)) == 1


def test_plan_improvements_finds_craft_source(tmp_path):
    run = _summary(tmp_path)
    root = _craft_root(tmp_path)
    plan = plan_improvements(run, crafts_prompt=root,
                             workspace_root=tmp_path / "ws")
    assert plan["push_status"] == "pending"
    assert len(plan["actions"]) == 1
    action = plan["actions"][0]
    assert action["craft"] == "plan"
    assert action["source_exists"] is True
    assert action["staged"].endswith("candidate/craft/plan/craft.md")


def test_apply_stages_and_records_history(tmp_path):
    run = _summary(tmp_path)
    root = _craft_root(tmp_path)
    ws = tmp_path / "ws"
    record = apply_improvements(
        run, crafts_prompt=root, workspace_root=ws,
        test_core=False, verify=False,
    )
    assert record["status"] == "staged"
    assert record["push_status"] == "pending"
    staged = ws / "improve" / "run" / "candidate" / "craft" / "plan" / "craft.md"
    assert staged.is_file()
    text = staged.read_text(encoding="utf-8")
    assert "## Staged improvement" in text
    assert "Fix craft plan." in text
    history = json.loads((ws / "improve" / "history.json").read_text())
    assert history[-1]["run_id"] == "run"
    assert history[-1]["push_status"] == "pending"


def test_apply_tests_with_core_and_verifies_with_nunnan(tmp_path):
    run = _summary(tmp_path)
    root = _craft_root(tmp_path)
    ws = tmp_path / "ws"
    calls = {}

    def core(goal, craft, staged_root, **kwargs):
        calls["core"] = (goal, craft, str(staged_root))
        return {"ok": True}

    def verify(staged_root, workers, **kwargs):
        calls["verify"] = (str(staged_root), list(workers))
        return {"ok": True, "exit_code": 0}

    record = apply_improvements(
        run, crafts_prompt=root, workspace_root=ws,
        core_runner=core, verify_fn=verify,
    )
    assert record["status"] == "verified"
    assert record["push_status"] == "pending"
    assert calls["core"][1] == "plan"
    assert calls["verify"][1] == ["vizhi"]
    assert record["history"].endswith("history.json")


def test_apply_stops_before_verify_when_core_fails(tmp_path):
    run = _summary(tmp_path)
    root = _craft_root(tmp_path)
    called = {"verify": False}

    def core(goal, craft, staged_root, **kwargs):
        return {"ok": False, "error": "bad"}

    def verify(*args, **kwargs):
        called["verify"] = True
        return {"ok": True}

    record = apply_improvements(
        run, crafts_prompt=root, workspace_root=tmp_path / "ws",
        core_runner=core, verify_fn=verify,
    )
    assert record["status"] == "test_failed"
    assert called["verify"] is False
