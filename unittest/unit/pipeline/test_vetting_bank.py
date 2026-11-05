"""Reviewed case banks: loader, grounding self-consistency, aggregation."""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from pipeline.vetting.utils.bank import list_banks, load_bank, resolve_bank
from pipeline.vetting.utils.models import Case, RunSummary, WorkerResult
from pipeline.vetting.utils.prompting import validate_plan
from pipeline.vetting.utils.runner import _aggregate, _run_one, repeat_vetting

MANIFESTS = Path(__file__).resolve().parents[3] / "nak_host" / "manifests"


class _FakeTarget:
    def __init__(self, answer="a grounded answer"):
        self.entry = {"name": "w", "decision": {}}
        self.history = []
        self._answer = answer

    def chat(self, question, session_id=None):
        return {"answer": self._answer}


class _FakeNunnan:
    def __init__(self, case_id="qb1", score=1.0):
        self.entry = {}
        self.history = []
        self.chats = []
        self._case_id = case_id
        self._score = score

    def chat(self, prompt):
        self.chats.append(prompt)
        return json.dumps({"case_id": self._case_id, "score": self._score,
                           "rationale": "good"})


def test_shipped_banks_load_and_match_workers():
    banks = {e["worker"]: e for e in list_banks()}
    assert {"arivu", "ezhilan"} <= set(banks)
    for name in ("arivu", "ezhilan"):
        loaded = load_bank(name)
        assert loaded["worker"] == name
        assert loaded["version"] >= 1
        assert len(loaded["cases"]) >= 2


def test_bank_version_selection_and_errors(tmp_path):
    assert load_bank("arivu.v1")["version"] == 1
    with pytest.raises(ValueError, match="no bank"):
        load_bank("ghost")
    with pytest.raises(ValueError, match="no bank"):
        load_bank("..")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"worker": "w"}), encoding="utf-8")
    with pytest.raises(ValueError, match="cases"):
        load_bank(bad)


def test_banks_are_grounded_in_their_workers_crafts():
    from nak_host.harness.craft.craft import crafts_for_worker
    from pipeline.vetting.utils.runner import build_profile

    for name in ("arivu", "ezhilan", "aayan"):
        allowed = [c["name"] for c in crafts_for_worker(name)]
        assert allowed, f"no crafts resolved for {name}"
        profile = build_profile(name, MANIFESTS / "workers.json",
                                MANIFESTS / "permissions.json")
        visible = profile.get("worker_visible") or {}
        modes = ((visible.get("autonomy") or {}).get("modes") or [])
        assert modes, f"no modes resolved for {name}"
        bank = load_bank(name)
        payload = {"cases": [c.to_dict() for c in bank["cases"]]}
        cases = validate_plan(payload, len(bank["cases"]), allowed, False,
                              modes)
        assert len(cases) == len(bank["cases"])


def test_resolve_bank_match_and_mismatch(tmp_path):
    assert resolve_bank(None, "arivu") is None
    cases = resolve_bank("arivu", "arivu")
    assert len(cases) >= 2
    with pytest.raises(ValueError, match="not 'arivu'"):
        resolve_bank("ezhilan", "arivu")
    one = tmp_path / "solo.v3.json"
    one.write_text(json.dumps({
        "worker": "solo", "version": 3,
        "cases": [{"id": "q1", "difficulty": "easy", "question": "Q?",
                    "expected": "E.", "rubric": "1.0 when ok; 0.0 otherwise.",
                    "weight": 1, "checks": []}]}), encoding="utf-8")
    assert load_bank(one)["version"] == 3
    assert len(resolve_bank(one, "solo")) == 1
    with pytest.raises(ValueError, match="not 'other'"):
        resolve_bank(one, "other")


def _summary(name, score, passed, run_dir="r"):
    return RunSummary(run_dir=run_dir, started_at="", workers=[
        WorkerResult(name=name, score=score, passed=passed, status="pass")])


def test_aggregate_median_and_majority(tmp_path):
    parent = tmp_path / "agg"
    import pipeline.vetting.utils.runner as runner_module

    summaries = [
        _summary("arivu", 0.9, True, "r1"),
        _summary("arivu", 0.5, False, "r2"),
        _summary("arivu", 0.8, True, "r3"),
    ]
    out = runner_module._aggregate(parent, summaries, "arivu", 3, "t0")
    assert len(out.workers) == 1
    worker = out.workers[0]
    assert worker.median == 0.8
    assert worker.lowest == 0.5
    assert (worker.passes, worker.repeats) == (2, 3)
    assert worker.verdict == "PASS"
    assert out.exit_code == 0
    assert (parent / "aggregate.json").is_file()
    assert (parent / "aggregate.md").is_file()


def test_aggregate_minority_fails(tmp_path):
    import pipeline.vetting.utils.runner as runner_module

    out = runner_module._aggregate(
        tmp_path / "agg",
        [_summary("w", 0.4, False, "r1"), _summary("w", 0.9, True, "r2")],
        "", 2, "t0")
    assert out.workers[0].verdict == "FAIL"
    assert out.exit_code == 1


def test_repeat_vetting_rejects_bad_counts(tmp_path):
    with pytest.raises(ValueError, match="repeats"):
        repeat_vetting(["arivu"], run_dir=tmp_path / "x", repeats=0)
    with pytest.raises(ValueError, match="repeats"):
        repeat_vetting(["arivu"], run_dir=tmp_path / "x", repeats="many")


def test_run_one_uses_bank_without_planning(tmp_path):
    nun = _FakeNunnan()
    bank_cases = [Case(id="qb1", difficulty="easy", question="Q?",
                       expected="E.",
                       rubric="1.0 when ok; 0.0 otherwise.", weight=1.0,
                       checks=[])]
    result = _run_one(
        "arivu", "run1", tmp_path, workers_file=MANIFESTS / "workers.json",
        permissions_file=MANIFESTS / "permissions.json",
        assessor_brain=None, target_brain=None,
        nunnan_factory=lambda b: nun,
        worker_factory=lambda n, b: _FakeTarget(),
        cases=1, threshold=0.6, answer_chars=8000,
        probe_crafts_enabled=False, probe_tools_enabled=False,
        bank_cases=bank_cases)
    assert [c.id for c in result.cases] == ["qb1"]
    assert len(nun.chats) == 1  # judgement only; no plan generation
    assert result.passed is True and result.status == "pass"
    transcript = (tmp_path / "arivu" / "output" / "evidence"
                  / "transcript.jsonl").read_text(encoding="utf-8")
    assert '"bank": true' in transcript


def test_case_invalid_routes_to_case_improvement():
    from pipeline.vetting.utils.models import Case as CaseModel
    from pipeline.vetting.utils.models import Judgement as JudgementModel
    from pipeline.vetting.utils.runner import _build_improvements

    result = WorkerResult(
        name="w",
        cases=[CaseModel(id="q1", difficulty="easy", question="Q?",
                         expected="E.", rubric="R.")],
        judgements=[JudgementModel(
            case_id="q1", score=0.0,
            rationale="CASE-INVALID: expected contradicts profile")])
    improvements = _build_improvements(result, None)
    assert len(improvements) == 1
    assert improvements[0].source == "case"
    assert improvements[0].target == "q1"


def test_profile_facts_reads_both_profile_layouts():
    from pipeline.vetting.utils.runner import _profile_facts

    profile = {
        "do_not": ["top-level do"],
        "values": ["top-level value"],
        "worker_visible": {
            "crafts": [{"name": "plan"}, {"name": "build"}],
            "autonomy": {"modes": ["plan", "build"], "max_steps": 3,
                         "auto_steps": 2, "approval": "small"},
            "do_not": ["visible do"],
            "values": ["visible value"],
        },
    }
    facts = _profile_facts(profile, "arivu")
    assert facts["worker"] == "arivu"
    assert facts["crafts"] == ["plan", "build"]
    assert facts["modes"] == ["plan", "build"]
    assert (facts["max_steps"], facts["auto_steps"],
            facts["approval"]) == (3, 2, "small")
    assert "visible do" in facts["do_not"]
    assert "visible value" in facts["values"]
    assert _profile_facts(None, "")["crafts"] == []


class _SeqNunnan:
    def __init__(self, replies):
        self.entry = {}
        self.history = []
        self._replies = list(replies)
        self.chats = []

    def chat(self, prompt):
        self.chats.append(prompt)
        reply = self._replies.pop(0) if self._replies else '{"cases": []}'
        return reply


def _run_plan_only(tmp_path, replies):
    from pipeline.vetting.utils.runner import _run_one

    nun = _SeqNunnan(replies)
    return _run_one(
        "arivu", "run9", tmp_path, workers_file=MANIFESTS / "workers.json",
        permissions_file=MANIFESTS / "permissions.json",
        assessor_brain=None, target_brain=None,
        nunnan_factory=lambda b: nun,
        worker_factory=lambda n, b: (_ for _ in ()).throw(
            AssertionError("target must not be built")),
        cases=1, threshold=0.6, answer_chars=8000,
        probe_crafts_enabled=False, probe_tools_enabled=False), nun


def test_identical_bad_plans_stop_early(tmp_path):
    bad = '{"cases": []}'
    result, nun = _run_plan_only(tmp_path, [bad, bad, bad])
    assert len(nun.chats) == 2
    assert result.status == "inconclusive"
    assert "plan failed" in result.error


def test_changing_bad_plans_use_all_attempts(tmp_path):
    bad1 = '{"cases": []}'
    bad2 = '{"cases": [{"id": "1"}]}'
    bad3 = '{"cases": [{"id": "q1", "difficulty": "easy"}]}'
    result, nun = _run_plan_only(tmp_path, [bad1, bad2, bad3])
    assert len(nun.chats) == 3
    assert result.status == "inconclusive"


def test_repeat_vetting_runs_each_pass(monkeypatch, tmp_path):
    import pipeline.vetting.utils.runner as runner_module

    calls = []

    def fake_run(workers, run_dir=None, **kwargs):
        calls.append(str(run_dir))
        return _summary("arivu", 1.0, True, str(run_dir))

    monkeypatch.setattr(runner_module, "run_vetting", fake_run)
    out = repeat_vetting(["arivu"], run_dir=tmp_path / "parent", repeats=2,
                         cases_from="arivu")
    assert len(calls) == 2
    assert calls[0].endswith("repeat-01") and calls[1].endswith("repeat-02")
    assert out.workers[0].verdict == "PASS"
    assert out.exit_code == 0
