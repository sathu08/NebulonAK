import json

import pytest

from pipeline.vetting.utils.models import Case
from pipeline.vetting.utils.prompting import (
    PromptError,
    build_judge_prompt,
    build_plan_prompt,
    build_plan_retry_prompt,
    fence_untrusted,
    parse_json_object,
    validate_judgement,
    validate_plan,
)


def _case(cid="q1", difficulty="easy"):
    return {
        "id": cid,
        "difficulty": difficulty,
        "question": "What is the goal?",
        "expected": "A grounded answer.",
        "rubric": "1.0 when grounded; 0.0 otherwise.",
        "weight": 1,
        "checks": [{"id": f"{cid}-c", "op": "contains", "expected": "goal"}],
    }


def _plan():
    return {"cases": [_case(f"q{i}", d) for i, d in enumerate(
        ["easy", "easy", "mid", "mid", "hard", "hard"], 1)]}


def test_validate_plan_accepts_balanced_six():
    cases = validate_plan(_plan(), 6)
    assert len(cases) == 6
    assert {c.difficulty for c in cases} == {"easy", "mid", "hard"}
    assert cases[0].checks[0]["op"] == "contains"


def _craft_case(question):
    case = _case()
    case["question"] = question
    return {"cases": [case]}


def test_validate_plan_rejects_unlisted_craft():
    plan = _craft_case("Explain the plan craft approval flow.")
    with pytest.raises(PromptError, match="only has"):
        validate_plan(plan, 1, ["test", "validate"])


def test_validate_plan_accepts_listed_craft():
    plan = _craft_case("Explain the test craft approval flow.")
    cases = validate_plan(plan, 1, ["test", "validate"])
    assert len(cases) == 1


def test_validate_plan_ignores_generic_craft_prose():
    plan = _craft_case("Describe the craft of writing good tests.")
    assert len(validate_plan(plan, 1, ["test", "validate"])) == 1


def test_validate_plan_grounding_is_opt_in():
    plan = _craft_case("Explain the plan craft approval flow.")
    assert len(validate_plan(plan, 1)) == 1
    assert len(validate_plan(plan, 1, [])) == 1


def _four_without_hard():
    return {"cases": [_case("q1", "easy"), _case("q2", "easy"),
                      _case("q3", "mid"), _case("q4", "mid")]}


def test_validate_plan_distribution_opt_out():
    with pytest.raises(PromptError, match="missing difficulties"):
        validate_plan(_four_without_hard(), 4)
    assert len(validate_plan(_four_without_hard(), 4, None, False)) == 4


def _modes_case(text):
    case = _case()
    case["question"] = text
    return {"cases": [case]}


def test_validate_plan_rejects_wrong_mode_count():
    plan = _modes_case("Name the three modes and what each allows.")
    with pytest.raises(PromptError, match="claims .* mode"):
        validate_plan(plan, 1, None, False, ["plan", "build"])


def test_validate_plan_accepts_matching_mode_count():
    plan = _modes_case("Name the two modes and what each allows.")
    assert len(validate_plan(plan, 1, None, False, ["plan", "build"])) == 1


def test_validate_plan_ignores_step_limits_and_missing_modes():
    plan = _modes_case("Stay within the three-step limit.")
    assert len(validate_plan(plan, 1, None, False, ["plan", "build"])) == 1
    other = _modes_case("Name the three modes.")
    assert len(validate_plan(other, 1)) == 1


def test_validate_plan_ignores_rubric_tier_language():
    case = _case()
    case["question"] = "Explain the modes."
    case["expected"] = "Names the modes."
    case["rubric"] = ("1.0 if both modes are named; "
                      "0.5 if only one mode is named; 0.0 otherwise.")
    plan = {"cases": [case]}
    assert len(validate_plan(plan, 1, None, False, ["plan", "build"])) == 1


def test_build_judge_prompt_renders_facts():
    from pipeline.vetting.utils.models import Case as CaseModel

    case = CaseModel(id="q1", question="Q?", expected="E.", rubric="R.",
                     difficulty="easy")
    plain = build_judge_prompt(case, "answer")
    assert "Worker facts" in plain
    filled = build_judge_prompt(case, "answer",
                                profile_facts={"worker": "w",
                                               "crafts": ["plan"]})
    assert '"crafts"' in filled and '"plan"' in filled


def test_validate_plan_requires_exact_count():
    plan = _plan()
    plan["cases"] = plan["cases"][:5]
    with pytest.raises(PromptError, match="exactly 6 cases"):
        validate_plan(plan, 6)


def test_validate_plan_rejects_numeric_id():
    plan = _plan()
    plan["cases"][0]["id"] = "1"
    with pytest.raises(PromptError, match="must start with a letter"):
        validate_plan(plan, 6)


def test_validate_plan_rejects_wrong_distribution():
    plan = _plan()
    plan["cases"][0]["difficulty"] = "hard"
    with pytest.raises(PromptError, match="2 easy, 2 mid and 2 hard"):
        validate_plan(plan, 6)


def test_validate_plan_rejects_duplicate_ids():
    plan = _plan()
    plan["cases"][1]["id"] = plan["cases"][0]["id"]
    with pytest.raises(PromptError, match="unique"):
        validate_plan(plan, 6)


def test_validate_plan_rejects_bad_check_op():
    plan = _plan()
    plan["cases"][0]["checks"][0]["op"] = "eval"
    with pytest.raises(PromptError, match="bad op"):
        validate_plan(plan, 6)


def test_validate_plan_drops_brittle_check_phrases():
    for phrase in ("personality", "laya", "max_steps", "plan"):
        plan = _plan()
        plan["cases"][0]["checks"][0]["expected"] = phrase
        cases = validate_plan(plan, 6)
        assert cases[0].checks == []


def test_parse_json_object_preserves_braces_inside_strings():
    raw = json.dumps({"cases": [{
        "id": "q1", "difficulty": "easy",
        "question": "What does {foo: bar} mean?",
        "expected": "E", "rubric": "R", "weight": 1.0,
    }]})
    parsed = parse_json_object(raw)
    assert parsed["cases"][0]["question"] == "What does {foo: bar} mean?"


def test_parse_json_object_handles_fences_and_prose():
    raw = 'Here you go:\n```json\n{"cases": []}\n```\nDone.'
    assert parse_json_object(raw) == {"cases": []}


def test_parse_json_object_merges_repeated_cases_keys():
    first = json.dumps({"cases": [_case("q1")]})
    second = json.dumps({"cases": [_case("q2")]})
    raw = first[:-1] + "," + second[1:]
    parsed = parse_json_object(raw)
    assert [case["id"] for case in parsed["cases"]] == ["q1", "q2"]


def test_parse_json_object_repairs_bare_check_value():
    raw = (
        '{"cases":[{"id":"q1","difficulty":"easy","question":"Q?",'
        '"expected":"E","rubric":"R","checks":[{"op":"contains","goal"}]}]}'
    )
    parsed = parse_json_object(raw)
    assert parsed["cases"][0]["checks"] == [
        {"op": "contains", "expected": "goal"}
    ]


def test_parse_json_object_repairs_missing_key_quotes():
    raw = (
        '{"cases":[{"id":"q1","difficulty":"easy","question":"Q?",'
        'expected":"E",rubric:"R","weight":1.0}]}'
    )
    parsed = parse_json_object(raw)
    assert parsed["cases"][0]["expected"] == "E"
    assert parsed["cases"][0]["rubric"] == "R"


def test_parse_json_object_recovers_top_level_case_fields():
    raw = (
        '{"cases":[{"id":"q1","difficulty":"easy","question":"Q1?",'
        '"expected":"E1","rubric":"R1",'
        '"checks":[{"id":"q1-check-1","op":"contains","expected":"x"}]}],'
        '"id":"q2","difficulty":"mid","question":"Q2?","expected":"E2",'
        '"rubric":"R2","checks":[{"id":"q2-check-1","op":"contains",'
        '"expected":"y"}],"weight":1.0}'
    )
    parsed = parse_json_object(raw)
    assert [case["id"] for case in parsed["cases"]] == ["q1", "q2"]
    assert parsed["cases"][1]["question"] == "Q2?"


def test_parse_json_object_recovers_cases_after_early_array_close():
    cases = [_case(f"q{i}") for i in range(1, 5)]
    raw = (
        '{"cases":[' + json.dumps(cases[0]) + "," + json.dumps(cases[1]) + "]},"
        + json.dumps(cases[2]) + "," + json.dumps(cases[3]) + "}"
    )
    parsed = parse_json_object(raw)
    assert [case["id"] for case in parsed["cases"]] == ["q1", "q2", "q3", "q4"]


def test_parse_json_object_closes_truncated_object():
    raw = ('{"case_id":"q1","score":1.0,"rationale":"ok",'
           '"evidence":"quote"')
    parsed = parse_json_object(raw)
    assert parsed["score"] == 1.0
    assert parsed["case_id"] == "q1"


def test_parse_json_object_rejects_empty():
    with pytest.raises(PromptError, match="empty response"):
        parse_json_object("   ")


def test_validate_judgement_bounds_score():
    case = Case(id="q1", question="Q", expected="E", rubric="R",
                difficulty="easy")
    with pytest.raises(PromptError, match="score must be a number"):
        validate_judgement({"case_id": "q1", "score": 2,
                            "rationale": "too high"}, case)
    ok = validate_judgement({"case_id": "q1", "score": 0.5,
                             "rationale": "half", "evidence": "quote"}, case)
    assert ok.score == 0.5


def test_fence_grows_past_inner_backticks():
    fenced = fence_untrusted("a ``` b")
    assert fenced.startswith("````text")
    assert fenced.endswith("````")


def test_judge_prompt_uses_the_actual_case_id():
    judge = build_judge_prompt(
        Case(id="q2", question="Q?", expected="E", rubric="R",
             difficulty="mid"), "answer", "")
    assert '"case_id": "q2"' in judge
    assert '"case_id": "q1"' not in judge


def test_judge_prompt_includes_policy_outcome_and_checks():
    judge = build_judge_prompt(
        Case(id="q2", question="Q?", expected="E", rubric="R",
             difficulty="mid"), "blocked", "",
        {"action": "escalate", "blocked": True},
        [{"id": "c1", "op": "contains", "expected": "x", "passed": False}])
    assert "Policy outcome" in judge
    assert "escalate" in judge
    assert "Deterministic checks" in judge
    assert '"passed": false' in judge


def test_judge_prompt_omits_case_checks_by_default():
    judge = build_judge_prompt(
        Case(id="q2", question="Q?", expected="E", rubric="R",
             difficulty="mid",
             checks=[{"id": "leaked-check", "op": "contains",
                      "expected": "x"}]), "answer", "")
    assert "leaked-check" not in judge


def test_plan_retry_prompt_includes_the_validator_error():
    prompt = build_plan_retry_prompt({"role": "researcher"}, 6,
                                     '{"cases": []}',
                                     "plan must contain exactly 6 cases (got 0)")
    assert "exactly 6 cases" in prompt
    assert "researcher" in prompt
    assert "2 easy, 2 mid, 2 hard" in prompt


def test_prompts_include_counts_and_profile():
    prompt = build_plan_prompt({"role": "researcher"}, 6)
    assert "2 easy, 2 mid, 2 hard" in prompt
    assert "researcher" in prompt
    assert "q1" in prompt
    assert "Prefer `checks: []`" in prompt
    assert "Never test hidden or unstable metadata" in prompt
    judge = build_judge_prompt(
        Case(id="q1", question="Q?", expected="E", rubric="R",
             difficulty="mid"), "answer", "")
    assert '"case_id"' in judge
    assert "answer" in judge
