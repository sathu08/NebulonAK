"""LLM router parsing: triage fields must reach policy.evaluate()."""
import json

from nak_host.decision.decider import _llm_prompt, _parse_llm_answer, decide_task
from nak_host.decision.policy import evaluate

WORKERS = ["vizhi", "arivu"]
TOOLS = ["file", "terminal"]


def _answer(**extra):
    data = {"worker": "vizhi", "tool": "file",
            "arguments": {"action": "read", "path": "a.txt"},
            "reason": "r"}
    data.update(extra)
    return json.dumps(data)


def test_parse_extracts_and_clamps_triage_fields():
    out = _parse_llm_answer(
        _answer(priority=4, risk=-1, needs_clarification=True, confidence=1.4),
        WORKERS, TOOLS)
    assert out["priority"] == 3.0
    assert out["risk"] == 0.0
    assert out["needs_clarification"] is True
    assert out["worker_confidence"] == 1.0
    assert out["tool_confidence"] == 1.0


def test_parse_ignores_bad_triage_values():
    out = _parse_llm_answer(
        _answer(priority="high", risk=None, needs_clarification="maybe",
                confidence="x"), WORKERS, TOOLS)
    for key in ("priority", "risk", "needs_clarification",
                "worker_confidence", "tool_confidence"):
        assert key not in out


def test_parse_accepts_string_booleans():
    out = _parse_llm_answer(_answer(needs_clarification="yes"), WORKERS, TOOLS)
    assert out["needs_clarification"] is True


def test_llm_prompt_requests_policy_fields():
    prompt = _llm_prompt("do x", None, WORKERS, TOOLS)
    for key in ("priority", "risk", "needs_clarification", "confidence"):
        assert f'"{key}"' in prompt


class _Brain:
    def __init__(self, answer):
        self.answer = answer

    def chat(self, prompt, **kwargs):
        return {"answer": self.answer}


def _workers_file(tmp_path):
    wf = tmp_path / "workers.json"
    wf.write_text("[]", encoding="utf-8")
    return str(wf)


def test_decide_task_feeds_policy_risk(tmp_path):
    res = decide_task(
        "ship it", llm=True,
        brain=_Brain(_answer(priority=1.0, risk=2.0,
                             needs_clarification=False, confidence=0.9)),
        workers=WORKERS, tools=TOOLS, workers_file=_workers_file(tmp_path))
    assert res["decision"]["risk"] == 2.0
    assert res["decision"]["priority"] == 1.0
    assert evaluate(res["decision"], {"max_risk": 2.0})["action"] == "escalate"


def test_decide_task_low_confidence_clarifies(tmp_path):
    res = decide_task(
        "do x", llm=True, brain=_Brain(_answer(confidence=0.2)),
        workers=WORKERS, tools=TOOLS, workers_file=_workers_file(tmp_path))
    verdict = evaluate(res["decision"], {"min_confidence": 0.6})
    assert verdict["action"] == "clarify"
