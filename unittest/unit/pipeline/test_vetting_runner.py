import json

import pipeline.vetting.utils.runner

from pipeline.vetting.utils.models import Answer, Case, Judgement
from pipeline.vetting.utils.runner import (
    _case_score,
    _normalize_contains,
    _run_checks,
    build_profile,
    planner_profile,
    run_vetting,
)


def _plan_json():
    cases = []
    for i, difficulty in enumerate(
            ["easy", "easy", "mid", "mid", "hard", "hard"], 1):
        cases.append({
            "id": f"q{i}",
            "difficulty": difficulty,
            "question": f"Question {i}?",
            "expected": f"Answer {i}",
            "rubric": "Score 1.0 when the answer is present.",
            "weight": 1,
            "checks": [{"id": f"q{i}-check", "op": "contains",
                        "expected": "answer"}],
        })
    return json.dumps({"cases": cases})


def _judge_json(case_id, score=1.0):
    return json.dumps({
        "case_id": case_id,
        "score": score,
        "rationale": "Matches the rubric.",
        "evidence": "answer",
    })


class ScriptedBrain:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def chat(self, text, messages=None, session_id=None):
        self.prompts.append(text)
        if not self.responses:
            raise AssertionError("unexpected brain call")
        return {"answer": self.responses.pop(0)}


class FlakyBrain(ScriptedBrain):
    def __init__(self, responses, failures=1):
        super().__init__(responses)
        self.failures = failures

    def chat(self, text, messages=None, session_id=None):
        if self.failures:
            self.failures -= 1
            self.prompts.append(text)
            raise RuntimeError("temporary timeout")
        return super().chat(text, messages, session_id)


class FakeAssessor:
    def __init__(self, brain):
        self.brain = brain
        self.entry = {"decision": {"enabled": True}, "memory": {"enabled": True}}

    def chat(self, text, **kwargs):
        return self.brain.chat(text)


class FakeTarget:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, text, session_id=None, **kwargs):
        if not self.responses:
            raise AssertionError("unexpected target call")
        return self.responses.pop(0)


def _run(tmp_path, target_responses, judge_score=1.0):
    judge_responses = [_judge_json(f"q{i}", judge_score) for i in range(1, 7)]
    nunnan_brain = ScriptedBrain([_plan_json(), *judge_responses])
    run_dir = tmp_path / "run"
    return run_vetting(
        ["vizhi"],
        run_dir=run_dir,
        brain=object(),
        nunnan_brain=nunnan_brain,
        nunnan_factory=lambda brain: FakeAssessor(brain),
        worker_factory=lambda name, brain: FakeTarget(target_responses),
        probe_crafts_enabled=False,
        probe_tools_enabled=False,
    )


def test_profile_separates_worker_visible_context():
    profile = build_profile("arivu")
    visible = profile["worker_visible"]
    assert visible["role"] == "developer"
    assert visible["crafts"]
    assert "modes" in visible["autonomy"]
    assert "tools" not in visible
    assert "tool_verdicts" not in visible
    assert "capabilities" not in visible


def test_planner_profile_drops_hidden_metadata():
    profile = build_profile("arivu")
    plan_profile = planner_profile(profile)
    assert plan_profile["worker_visible"] == profile["worker_visible"]
    for hidden in ("tools", "tool_verdicts", "capabilities",
                   "decision", "memory"):
        assert hidden not in plan_profile


def test_plan_prompt_omits_assessor_only_tool_metadata(tmp_path, monkeypatch):
    captured = {}
    original = pipeline.vetting.utils.runner.build_plan_prompt

    def spy(profile, cases):
        captured.update(profile)
        return original(profile, cases)

    monkeypatch.setattr("pipeline.vetting.utils.runner.build_plan_prompt", spy)
    answers = [{"answer": f"answer {i}"} for i in range(1, 7)]
    _run(tmp_path, answers)
    assert "worker_visible" in captured
    assert "tools" not in captured
    assert "tool_verdicts" not in captured
    assert "capabilities" not in captured
    assert "decision" not in captured
    assert "memory" not in captured


def test_contains_checks_ignore_markdown_and_punctuation(tmp_path):
    case = Case(
        id="q1", question="Name the crafts", expected="plan and build",
        rubric="Both crafts must appear", difficulty="easy",
        checks=[{"id": "c1", "op": "contains",
                 "expected": "plan and build"}],
    )
    answer = Answer(case_id="q1",
                    text="The two crafts are `plan` and **build**.")
    checks = _run_checks([case], [answer], tmp_path)
    assert checks[0]["passed"] is True
    assert _normalize_contains("`plan` and **build**") == "plan and build"


def test_case_score_excludes_judge_errors():
    cases = [
        Case(id="q1", question="Q1", expected="E", rubric="R",
             difficulty="easy"),
        Case(id="q2", question="Q2", expected="E", rubric="R",
             difficulty="mid"),
    ]
    answers = [Answer(case_id="q1", text="a"), Answer(case_id="q2", text="b")]
    judgements = [
        Judgement(case_id="q1", score=1.0, rationale="ok"),
        Judgement(case_id="q2", score=0.0, error="invalid JSON"),
    ]
    assert _case_score(cases, judgements, answers) == 1.0
    assert _case_score(cases[1:], judgements[1:], answers) is None


def test_empty_triage_makes_the_case_inconclusive(tmp_path):
    responses = [{
        "answer": "I've flagged this for review.",
        "triage": {"ok": True, "decision": {},
                   "policy": {"action": "escalate"}},
    } for _ in range(6)]
    brain = ScriptedBrain([_plan_json()])
    summary = run_vetting(
        ["vizhi"],
        run_dir=tmp_path / "run",
        brain=object(),
        nunnan_brain=brain,
        nunnan_factory=lambda b: FakeAssessor(b),
        worker_factory=lambda name, b: FakeTarget(responses),
        probe_crafts_enabled=False,
        probe_tools_enabled=False,
    )
    worker = summary.workers[0]
    assert worker.status == "inconclusive"
    assert worker.score == 0.0
    assert not worker.passed
    assert summary.exit_code == 3
    assert worker.structural["inconclusive_cases"] == 6
    assert all(answer.inconclusive for answer in worker.answers)
    assert not brain.responses


def test_target_decision_is_disabled_by_default(tmp_path):
    created = []

    class EntryTarget(FakeTarget):
        def __init__(self, responses):
            super().__init__(responses)
            self.entry = {"decision": {"enabled": True},
                          "memory": {"enabled": True}}

    def factory(name, brain):
        target = EntryTarget([{"answer": f"answer {i}"} for i in range(1, 7)])
        created.append(target)
        return target

    summary = run_vetting(
        ["vizhi"],
        run_dir=tmp_path / "run",
        brain=object(),
        nunnan_brain=ScriptedBrain([_plan_json(), *[
            _judge_json(f"q{i}") for i in range(1, 7)]]),
        nunnan_factory=lambda b: FakeAssessor(b),
        worker_factory=factory,
        probe_crafts_enabled=False,
        probe_tools_enabled=False,
    )
    assert summary.workers[0].passed
    assert created[0].entry["decision"]["enabled"] is False
    assert created[0].entry["memory"]["enabled"] is True


def test_run_writes_reports_history_and_transcript(tmp_path):
    answers = [{"answer": f"answer {i}"} for i in range(1, 7)]
    summary = _run(tmp_path, answers)
    worker = summary.workers[0]
    assert worker.passed
    assert worker.score == 1.0
    assert len(worker.cases) == 6
    assert len(worker.answers) == 6
    assert len(worker.judgements) == 6
    assert all(check["passed"] for check in worker.checks)
    assert worker.trace["scorecard"]["ok"] is True
    assert worker.trace["transcript"]["matched"] == 6

    run_dir = tmp_path / "run"
    assert (run_dir / "summary.md").exists()
    assert (run_dir / "summary.json").exists()
    evidence = run_dir / "vizhi" / "output" / "evidence"
    reports = run_dir / "vizhi" / "output" / "reports"
    assert (reports / "vizhi.md").exists()
    assert (reports / "vizhi.json").exists()
    history = json.loads((evidence / "nunnan_history.json").read_text())
    assert len(history) == 7
    assert history[0]["phase"] == "plan"
    lines = (evidence / "transcript.jsonl").read_text().splitlines()
    assert len(lines) == 14
    assert any('"type": "answer"' in line for line in lines)
    report = (reports / "vizhi.md").read_text()
    assert "Vetting report — vizhi" in report
    assert "PASS" in report


def _bad_plan_json(count=5):
    plan = json.loads(_plan_json())
    plan["cases"] = plan["cases"][:count]
    return json.dumps(plan)


def test_invalid_plan_is_retried_with_the_validator_error(tmp_path):
    judge = [_judge_json(f"q{i}") for i in range(1, 7)]
    brain = ScriptedBrain([_bad_plan_json(), _plan_json(), *judge])
    summary = run_vetting(
        ["vizhi"],
        run_dir=tmp_path / "run",
        brain=object(),
        nunnan_brain=brain,
        nunnan_factory=lambda b: FakeAssessor(b),
        worker_factory=lambda name, b: FakeTarget(
            [{"answer": f"answer {i}"} for i in range(1, 7)]),
        probe_crafts_enabled=False,
        probe_tools_enabled=False,
    )
    worker = summary.workers[0]
    assert worker.passed
    assert "exactly 6 cases" in brain.prompts[1]
    history = json.loads(
        (tmp_path / "run" / "vizhi" / "output" / "evidence"
         / "nunnan_history.json").read_text())
    assert [r["phase"] for r in history].count("plan") == 2


def test_transient_plan_error_is_retried(tmp_path):
    judge = [_judge_json(f"q{i}") for i in range(1, 7)]
    brain = FlakyBrain([_plan_json(), *judge], failures=1)
    summary = run_vetting(
        ["vizhi"],
        run_dir=tmp_path / "run",
        brain=object(),
        nunnan_brain=brain,
        nunnan_factory=lambda b: FakeAssessor(b),
        worker_factory=lambda name, b: FakeTarget(
            [{"answer": f"answer {i}"} for i in range(1, 7)]),
        probe_crafts_enabled=False,
        probe_tools_enabled=False,
    )
    worker = summary.workers[0]
    assert worker.passed
    history = json.loads(
        (tmp_path / "run" / "vizhi" / "output" / "evidence"
         / "nunnan_history.json").read_text())
    plans = [r for r in history if r["phase"] == "plan"]
    assert len(plans) == 2
    assert "temporary timeout" in plans[0]["error"]


def test_plan_retry_gives_up_after_three_attempts(tmp_path):
    bad = [_bad_plan_json() for _ in range(3)]
    bad[1] = bad[1].replace('"q1"', '"q1x"')
    bad[2] = bad[2].replace('"q1"', '"q1y"')
    brain = ScriptedBrain(bad)
    summary = run_vetting(
        ["vizhi"],
        run_dir=tmp_path / "run",
        brain=object(),
        nunnan_brain=brain,
        nunnan_factory=lambda b: FakeAssessor(b),
        worker_factory=lambda name, b: FakeTarget([]),
        probe_crafts_enabled=False,
        probe_tools_enabled=False,
    )
    worker = summary.workers[0]
    assert not worker.passed
    assert worker.status == "inconclusive"
    assert worker.score == 0.0
    assert summary.exit_code == 3
    assert "plan failed" in worker.error
    assert "exactly 6 cases" in worker.error
    assert not brain.responses
    report = (tmp_path / "run" / "vizhi" / "output" / "reports"
              / "vizhi.md").read_text()
    assert "INCONCLUSIVE" in report


def test_identical_bad_plans_stop_before_third_attempt(tmp_path):
    brain = ScriptedBrain([_bad_plan_json() for _ in range(3)])
    summary = run_vetting(
        ["vizhi"],
        run_dir=tmp_path / "run",
        brain=object(),
        nunnan_brain=brain,
        nunnan_factory=lambda b: FakeAssessor(b),
        worker_factory=lambda name, b: FakeTarget([]),
        probe_crafts_enabled=False,
        probe_tools_enabled=False,
    )
    worker = summary.workers[0]
    assert worker.status == "inconclusive"
    assert "plan failed" in worker.error
    assert len(brain.responses) == 1


def test_trace_failure_is_evidence_not_a_report_failure(tmp_path, monkeypatch):
    def boom(_args):
        raise RuntimeError("trace down")

    monkeypatch.setattr("pipeline.vetting.utils.runner.trace", boom)
    answers = [{"answer": f"answer {i}"} for i in range(1, 7)]
    summary = _run(tmp_path, answers)
    worker = summary.workers[0]
    assert worker.passed
    assert worker.error == ""
    assert worker.trace["transcript"]["ok"] is False
    assert "trace down" in worker.trace["transcript"]["error"]


def test_report_failure_marks_the_worker_failed(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("pipeline.vetting.utils.runner.write_worker_report", boom)
    answers = [{"answer": f"answer {i}"} for i in range(1, 7)]
    summary = _run(tmp_path, answers)
    worker = summary.workers[0]
    assert not worker.passed
    assert "report failed" in worker.error
    assert "disk full" in worker.error


def test_default_run_isolates_brain_users_per_target(tmp_path, monkeypatch):
    users = []

    class UserBrain(ScriptedBrain):
        def __init__(self, user=None):
            super().__init__([_plan_json(), *[
                _judge_json(f"q{i}") for i in range(1, 7)]])
            self.user = user
            self.ensured = False

        def ensure_user(self):
            self.ensured = True

    def fake_make_brain(*, user=None, **_kwargs):
        brain = UserBrain(user=user)
        users.append(brain)
        return brain

    monkeypatch.setattr("pipeline.vetting.utils.runner.make_brain", fake_make_brain)
    summary = run_vetting(
        ["vizhi", "aayan"],
        run_dir=tmp_path / "run",
        nunnan_factory=lambda b: FakeAssessor(b),
        worker_factory=lambda name, b: FakeTarget(
            [{"answer": f"answer {i}"} for i in range(1, 7)]),
        probe_crafts_enabled=False,
        probe_tools_enabled=False,
    )
    assert summary.exit_code == 0
    names = [brain.user for brain in users]
    assert len(names) == 4
    assert len(set(names)) == 4
    assert any("vizhi_assessor" in name for name in names)
    assert any("aayan_assessor" in name for name in names)
    assert any("vizhi_target" in name for name in names)
    assert any("aayan_target" in name for name in names)
    assert all(brain.ensured for brain in users)


def test_blocked_target_answer_is_recorded_not_fatal(tmp_path):
    responses = [{
        "answer": f"need more detail {i}",
        "triage": {"policy": {"action": "clarify"}},
    } for i in range(1, 7)]
    summary = _run(tmp_path, responses, judge_score=0.0)
    worker = summary.workers[0]
    assert worker.score == 0.0
    assert not worker.passed
    assert all(answer.blocked for answer in worker.answers)
    assert all(answer.blocked_by_policy for answer in worker.answers)
    assert all(answer.policy_action == "clarify" for answer in worker.answers)
    transcript = (tmp_path / "run" / "vizhi" / "output" / "evidence"
                  / "transcript.jsonl").read_text()
    assert '"blocked": true' in transcript


def test_low_scores_become_improvement_suggestions(tmp_path):
    answers = [{"answer": f"answer {i}"} for i in range(1, 7)]
    summary = _run(tmp_path, answers, judge_score=0.2)
    worker = summary.workers[0]
    assert worker.improvements
    assert all(item.worker == "vizhi" for item in worker.improvements)
    assert any(item.source == "case" for item in worker.improvements)
    md = (tmp_path / "run" / "vizhi" / "output" / "reports"
          / "vizhi.md").read_text()
    assert "## Where to improve" in md
