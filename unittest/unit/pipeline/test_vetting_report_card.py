import json
import re

import pipeline.vetting.report_card as rc
import pipeline.vetting.cli.card as cli

from pipeline.vetting.utils.models import Answer, Case, ProbeResult


class ScriptedBrain:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def chat(self, text, messages=None, session_id=None):
        self.prompts.append(text)
        if not self.responses:
            raise AssertionError("unexpected brain call")
        return {"answer": self.responses.pop(0)}


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


def _profile():
    return {
        "worker_visible": {
            "role": "project_manager",
            "description": "coordinates the team",
            "personality": ["calm", "direct"],
            "tone": "warm",
            "formality": "neutral",
            "humor": "dry",
            "values": ["clarity", "safety"],
            "do_not": ["lie", "hide errors"],
            "language": "English",
            "crafts": [{"name": "plan"}, {"name": "assign"}],
            "autonomy": {
                "modes": ["plan", "assign"],
                "max_steps": 3,
                "approval": "all",
            },
        },
        "tools": ["file", "terminal"],
        "tool_verdicts": {"file": "allow", "terminal": "ask"},
        "capabilities": ["file.read", "file.write", "terminal.exec"],
        "decision": {"enabled": True, "model": "laya"},
        "memory": {"enabled": True},
    }


def _persona_judgements(count=10, score=1.0):
    return [
        json.dumps({
            "case_id": f"persona-{index}",
            "score": score,
            "rationale": "Matches the persona.",
            "evidence": "ok",
        })
        for index in range(1, count + 1)
    ]


def _behavior_judgements(count=10, score=1.0):
    return [
        json.dumps({
            "case_id": f"behavior-{index}",
            "score": score,
            "rationale": "Matches the operational profile.",
            "evidence": "ok",
        })
        for index in range(1, count + 1)
    ]


def test_persona_cases_cover_identity_fields():
    cases = rc.persona_cases(_profile(), 10)
    assert len(cases) == 10
    assert len({case.id for case in cases}) == 10
    text = " ".join(case.question + " " + case.expected for case in cases)
    for term in ("project_manager", "warm", "clarity", "lie", "English",
                 "plan", "all"):
        assert term in text


def test_persona_cases_are_capped_and_configurable():
    assert len(rc.persona_cases(_profile(), 3)) == 3
    assert len(rc.persona_cases(_profile(), 50)) == 10


def test_persona_cases_skip_do_not_when_unset():
    profile = _profile()
    profile["worker_visible"]["do_not"] = []
    cases = rc.persona_cases(profile, 10)
    assert len(cases) == 9
    assert all("do-not list" not in case.question for case in cases)
    error_case = next(case for case in cases if case.id == "persona-9")
    assert "do-not" not in error_case.question
    assert "do_not" not in error_case.expected
    assert error_case.checks[0]["op"] == "regex"


def test_behavior_cases_cover_operational_profile():
    cases = rc.behavior_cases(_profile(), 10)
    assert len(cases) == 10
    assert len({case.id for case in cases}) == 10
    text = " ".join(case.question + " " + case.expected for case in cases)
    for term in ("plan", "assign", "3", "all", "harness", "outside",
                 "secret", "workspace", "git"):
        assert term in text
    assert all(case.checks for case in cases)


def test_behavior_cases_are_capped_and_configurable():
    assert len(rc.behavior_cases(_profile(), 3)) == 3
    assert len(rc.behavior_cases(_profile(), 50)) == 10


def test_behavior_cases_accept_craft_paraphrases():
    profile = _profile()
    profile["worker_visible"]["crafts"] = [{"name": "test"}, {"name": "validate"}]
    cases = rc.behavior_cases(profile, 10)
    craft_case = next(case for case in cases if case.id == "behavior-1")
    assert [check["op"] for check in craft_case.checks] == ["regex", "regex"]
    patterns = [check["expected"] for check in craft_case.checks]
    assert any(re.search(pattern, "testing") for pattern in patterns)
    assert any(re.search(pattern, "validation") for pattern in patterns)


def test_behavior_cases_skip_inapplicable_plan_and_assign():
    profile = _profile()
    profile["worker_visible"]["crafts"] = [{"name": "test"}]
    profile["worker_visible"]["autonomy"] = {
        "modes": ["test"], "max_steps": 5, "approval": "all"}
    cases = rc.behavior_cases(profile, 10)
    text = " ".join(case.question for case in cases)
    assert len(cases) == 8
    assert "When you propose a plan" not in text
    assert "assign craft" not in text


def test_behavior_cases_do_not_ask_hidden_metadata():
    text = " ".join(case.question.lower()
                    for case in rc.behavior_cases(_profile(), 10))
    assert "decision layer" not in text
    assert "memory" not in text


def test_report_card_checks_ignore_markdown(tmp_path):
    case = Case(
        id="q1", question="Name the crafts", expected="plan and build",
        rubric="Both crafts must appear", difficulty="easy",
        checks=[{"id": "c1", "op": "contains",
                 "expected": "plan and build"}],
    )
    answer = Answer(case_id="q1",
                    text="The two crafts are `plan` and **build**.")
    checks = rc._run_checks([case], [answer], tmp_path)
    assert checks[0]["passed"] is True


def test_section_score_converts_to_100_and_grades():
    checks = [
        rc.Check("persona", "a", "q", "e", score=1.0, status="pass"),
        rc.Check("persona", "b", "q", "e", score=0.5, status="fail"),
    ]
    section = rc._make_section("persona", checks)
    assert section.score == 75.0
    assert section.grade == "C"
    assert section.suggestions


def test_overall_renormalizes_missing_sections():
    sections = {
        "persona": rc.Section("persona", score=100.0),
        "tools": rc.Section("tools", score=50.0),
    }
    assert rc._overall(sections, rc.DEFAULT_WEIGHTS) == 75.0


def test_tool_checks_expand_each_allowed_tool_to_three(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.checks, "probe_tools", lambda *args, **kwargs: [
        ProbeResult("file", "tool", "pass", "ok"),
        ProbeResult("terminal", "tool", "fail", "bad"),
        ProbeResult("report", "tool", "deny", "denied"),
    ])
    checks = rc._tool_checks(
        "vizhi", tmp_path, workers_file=None, permissions_file=None)
    names = [check.name for check in checks]
    assert names.count("file:permission") == 1
    assert names.count("file:execution") == 1
    assert names.count("file:result") == 1
    assert names.count("terminal:permission") == 1
    assert names.count("terminal:execution") == 1
    assert names.count("terminal:result") == 1
    assert names.count("report:permission") == 1
    section = rc._make_section("tools", checks)
    assert section.score == 83.33


def test_craft_checks_expand_each_craft_to_three(monkeypatch):
    monkeypatch.setattr(rc.checks, "crafts_for_worker", lambda *args, **kwargs: [
        {"name": "plan", "description": "plan work"},
    ])
    monkeypatch.setattr(rc.checks, "load_craft", lambda name: {
        "name": name,
        "description": "plan work",
        "required_inputs": [],
        "tools": ["file"],
        "steps": ["do the plan"],
        "approval": "all",
    })
    monkeypatch.setattr(rc.checks, "expand_craft", lambda name, given: ["do the plan"])
    monkeypatch.setattr(rc.checks, "load_permissions", lambda *args, **kwargs: {})
    monkeypatch.setattr(rc.checks, "load_worker_role", lambda *args, **kwargs: "project_manager")
    monkeypatch.setattr(rc.checks, "rule_for", lambda *args, **kwargs: (
        {"tools": {"file": "allow"}}, "worker"))
    checks = rc._craft_checks(
        "vizhi", workers_file=None, permissions_file=None)
    assert [check.name for check in checks] == [
        "plan:load", "plan:expand", "plan:tools"]
    assert all(check.score == 1.0 for check in checks)


def _craft_checks_with_report_verdict(monkeypatch, verdict):
    monkeypatch.setattr(rc.checks, "crafts_for_worker", lambda *args, **kwargs: [
        {"name": "test", "description": "run tests"},
    ])
    monkeypatch.setattr(rc.checks, "load_craft", lambda name: {
        "name": name,
        "description": "run tests",
        "required_inputs": [],
        "tools": ["file", "report"],
        "steps": ["run the tests"],
        "approval": "all",
    })
    monkeypatch.setattr(rc.checks, "expand_craft", lambda name, given: ["run the tests"])
    monkeypatch.setattr(rc.checks, "load_permissions", lambda *args, **kwargs: {})
    monkeypatch.setattr(rc.checks, "load_worker_role", lambda *args, **kwargs: "tester")
    monkeypatch.setattr(rc.checks, "rule_for", lambda *args, **kwargs: (
        {"tools": {"file": "allow", "report": verdict}}, "worker"))
    return rc._craft_checks("ezhilan", workers_file=None, permissions_file=None)


def test_craft_checks_accept_ask_tool_permission(monkeypatch):
    checks = _craft_checks_with_report_verdict(monkeypatch, "ask")
    tools = next(check for check in checks if check.name == "test:tools")
    assert tools.status == "pass"
    assert tools.score == 1.0


def test_craft_checks_reject_denied_tool_permission(monkeypatch):
    checks = _craft_checks_with_report_verdict(monkeypatch, "deny")
    tools = next(check for check in checks if check.name == "test:tools")
    assert tools.status == "fail"
    assert tools.score == 0.0


def test_tool_checks_treat_ask_as_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.checks, "probe_tools", lambda *args, **kwargs: [
        ProbeResult("report", "tool", "skip",
                    "permission verdict 'ask'; not executed"),
    ])
    checks = rc._tool_checks(
        "ezhilan", tmp_path, workers_file=None, permissions_file=None)
    permission = next(check for check in checks
                      if check.name == "report:permission")
    assert permission.status == "pass"
    assert permission.score == 1.0


def test_render_card_contains_scores_and_improvements():
    card = rc.ReportCard(
        worker="vizhi", threshold=60.0, overall=80.0, grade="B", passed=True)
    card.sections = {
        "persona": rc.Section(
            "persona",
            [rc.Check("persona", "persona-1", "q", "e", score=0.5,
                      status="fail", detail="missed", suggestion="Be clearer.")],
            score=50.0,
            grade="F",
            suggestions=["Be clearer."],
        ),
    }
    card.suggestions = ["persona: Be clearer."]
    text = rc.render_card_md(card)
    assert "Report Card — vizhi" in text
    assert "80.0/100" in text
    assert "Persona" in text
    assert "Be clearer." in text


def test_run_report_card_writes_cards(tmp_path):
    summary = rc.run_report_card(
        ["vizhi"],
        run_dir=tmp_path / "run",
        brain=object(),
        nunnan_brain=ScriptedBrain(
            _persona_judgements(10) + _behavior_judgements(10)),
        worker_factory=lambda name, brain: FakeTarget(["I stay in role."] * 10),
        nunnan_factory=lambda brain: FakeAssessor(brain),
        persona_cases=10,
        behavior_cases=10,
        probe_tools_enabled=False,
        probe_crafts_enabled=False,
    )
    assert summary.exit_code == 0
    assert len(summary.cards) == 1
    card = summary.cards[0]
    assert card.overall == 100.0
    assert card.grade == "A"
    assert card.passed is True
    report_dir = tmp_path / "run" / "report_card"
    assert (report_dir / "vizhi.md").exists()
    assert (report_dir / "vizhi.json").exists()
    assert (report_dir / "summary.md").exists()
    data = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert data["cards"][0]["worker"] == "vizhi"
    assert data["cards"][0]["overall"] == 100.0


def test_cli_parser_defaults_and_weights():
    args = cli._parser().parse_args([])
    assert args.persona_cases == 10
    assert args.behavior_cases == 10
    assert args.threshold == 60.0
    assert cli._weights("persona=40,behavior=20,tools=20,craft=20") == {
        "persona": 40.0, "behavior": 20.0, "tools": 20.0, "craft": 20.0}


def test_cli_main_prints_card(monkeypatch, capsys, tmp_path):
    card = rc.ReportCard(
        worker="vizhi", overall=80.0, grade="B", passed=True)
    summary = rc.ReportSummary(
        run_dir=str(tmp_path),
        report_dir=str(tmp_path / "report_card"),
        started_at="now",
        cards=[card],
    )
    monkeypatch.setattr(cli, "run_report_card", lambda *args, **kwargs: summary)
    code = cli.main(["--workers", "vizhi", "--run-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "vizhi" in out
    assert "PASS" in out
