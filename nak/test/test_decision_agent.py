"""Tests for DecisionAgent (async, Mind-only) + AgentCreator confirm gate + AgentRegistry hybrid."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

try:
    import pytest  # type: ignore
except ImportError:
    pytest = None  # type: ignore

from nak.agents.DecisionAgent.models import DecisionResult
from nak.agents.DecisionAgent.agent import DecisionAgent
from nak.agents.AgentCreator.agent import AgentCreator
from nak.utils.agent_registry import AgentRegistry, AgentMeta


# -- helpers -----------------------------------------------------------------


class FakeBrain:
    def __init__(self, answer_json: str):
        self._answer = answer_json
        self.chat_calls = []
        self.remember_calls = []

    def chat(self, text, messages=None, session_id=None, user=None, conversation_id=None):
        self.chat_calls.append(text)
        return {"answer": self._answer, "turn": 1}

    def search(self, query, top_k=None, expand=False, user=None):
        return [{"memory": "dummy"}]

    def remember(self, text, **kwargs):
        self.remember_calls.append(text)
        return {"memory_id": "fake"}


async def always_confirm(spec: DecisionResult) -> bool:
    return True


async def never_confirm(spec: DecisionResult) -> bool:
    return False


# -- DecisionResult ----------------------------------------------------------


def test_decision_result_valid():
    r = DecisionResult(action="USE_AGENT", agent_name="ExcelAgent", reason="test", confidence=0.9)
    assert r.is_use
    assert r.confidence == 0.9
    d = r.to_dict()
    r2 = DecisionResult.from_dict(d)
    assert r2.action == "USE_AGENT"


def test_decision_result_invalid_action():
    try:
        DecisionResult(action="INVALID", agent_name=None)  # type: ignore
        assert False, "should have raised"
    except ValueError:
        pass


# -- DecisionAgent parse -----------------------------------------------------


def test_decision_agent_parse_strict_json():
    agent = DecisionAgent(brain=MagicMock(), registry=MagicMock())
    raw = json.dumps({"action": "USE_AGENT", "agent_name": "ExcelAgent", "reason": "x", "confidence": 0.94, "parameters": {}})
    r = agent._parse_response(raw)
    assert r.action == "USE_AGENT" and r.agent_name == "ExcelAgent"


def test_decision_agent_parse_markdown_wrapped():
    agent = DecisionAgent(brain=MagicMock(), registry=MagicMock())
    raw = '```json\n{"action":"CREATE_AGENT","agent_name":"PDFReaderAgent","reason":"no pdf agent","confidence":0.91}\n```'
    r = agent._parse_response(raw)
    assert r.action == "CREATE_AGENT" and r.agent_name == "PDFReaderAgent"


def test_decision_agent_parse_invalid_returns_error():
    agent = DecisionAgent(brain=MagicMock(), registry=MagicMock())
    try:
        agent._parse_response("not json at all")
        assert False
    except ValueError:
        pass


# -- DecisionAgent async decide (Mind-only) ----------------------------------


def test_decision_agent_decide_use_agent():
    brain = FakeBrain(json.dumps({"action": "USE_AGENT", "agent_name": "Example", "reason": "example can handle", "confidence": 0.92, "parameters": {}}))
    reg = MagicMock()
    reg.available_agents_str.return_value = "Example — minimal harness"
    agent = DecisionAgent(brain=brain, registry=reg)  # type: ignore

    result = asyncio.run(agent.decide("hello"))
    assert result.action == "USE_AGENT"
    assert result.agent_name == "Example"
    assert len(brain.chat_calls) == 1
    # prompt must contain available_agents and user_request (Mind-only, no openai)
    assert "Example" in brain.chat_calls[0]
    assert "hello" in brain.chat_calls[0]


def test_decision_agent_decide_create_agent():
    brain = FakeBrain(json.dumps({"action": "CREATE_AGENT", "agent_name": "ExcelAgent", "reason": "no excel agent", "confidence": 0.88}))
    reg = MagicMock()
    reg.available_agents_str.return_value = "Example"
    agent = DecisionAgent(brain=brain, registry=reg)  # type: ignore
    result = asyncio.run(agent.decide("Read this Excel and find duplicates"))
    assert result.is_create and result.agent_name == "ExcelAgent"


def test_decision_agent_decide_fallback_ask_on_invalid_json():
    brain = FakeBrain("not a json response!!!")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Example"
    agent = DecisionAgent(brain=brain, registry=reg)  # type: ignore
    result = asyncio.run(agent.decide("ambiguous", strict=False))
    assert result.action == "ASK_USER"
    assert result.confidence == 0.0


def test_decision_agent_strict_raises():
    brain = FakeBrain("bad json")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Example"
    agent = DecisionAgent(brain=brain, registry=reg)  # type: ignore
    try:
        asyncio.run(agent.decide("hi", strict=True))
        assert False
    except Exception:
        pass


def test_decision_agent_brain_error_fallback():
    from nak.brain.client import BrainError

    class ErrorBrain:
        def chat(self, *a, **kw):
            raise BrainError("unreachable", status=503, body={})

    reg = MagicMock()
    reg.available_agents_str.return_value = "Example"
    agent = DecisionAgent(brain=ErrorBrain(), registry=reg)  # type: ignore
    result = asyncio.run(agent.decide("hello", strict=False))
    assert result.action == "ASK_USER"
    assert "unreachable" in result.reason.lower() or "unavailable" in result.reason.lower()


# -- AgentRegistry -----------------------------------------------------------


def test_registry_register_and_list(tmp_path: Path = None):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        reg_path = td / "registry.json"
        agents_root = td / "agents"
        agents_root.mkdir()
        reg = AgentRegistry(registry_path=reg_path, agents_root=agents_root)
        assert reg.list_agents() == []
        reg.register(AgentMeta(name="ExcelAgent", description="excel", capabilities=["excel"]))
        agents = reg.list_agents()
        assert len(agents) == 1 and agents[0].name == "ExcelAgent"
        assert reg.exists("excelagent")
        assert "ExcelAgent" in reg.available_agents_str()


def test_registry_scan_disk(tmp_path: Path = None):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        agents_root = td / "agents"
        agents_root.mkdir()
        # create fake agents
        (agents_root / "ExcelAgent").mkdir()
        (agents_root / "ExcelAgent" / "__init__.py").write_text('"""Excel"""', encoding="utf-8")
        (agents_root / "Foo").mkdir()
        # no marker file -> not counted
        reg = AgentRegistry(registry_path=td / "registry.json", agents_root=agents_root)
        scanned = reg.scan_disk()
        names = [m.name for m in scanned]
        assert "ExcelAgent" in names
        assert "Foo" not in names


# -- AgentCreator confirm gate ----------------------------------------------


def test_agent_creator_needs_confirm(tmp_path: Path = None):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        agents_root = td / "agents"
        agents_root.mkdir()
        reg = AgentRegistry(registry_path=td / "registry.json", agents_root=agents_root)
        brain = FakeBrain(json.dumps({}))  # not used for create except remember mirror
        # patch remember to avoid Mind call
        brain.remember = MagicMock(return_value={"memory_id": "x"})
        creator = AgentCreator(brain=brain, registry=reg, agents_root=agents_root)  # type: ignore

        spec = DecisionResult(action="CREATE_AGENT", agent_name="ExcelAgent", reason="need excel", confidence=0.9)

        # not confirmed -> None, no folder
        result = asyncio.run(creator.create(spec, never_confirm))
        assert result is None
        assert not (agents_root / "ExcelAgent").exists()
        assert not reg.exists("ExcelAgent")

        # confirmed -> creates
        result = asyncio.run(creator.create(spec, always_confirm))
        assert result is not None and result.exists()
        assert (result / "__init__.py").exists()
        assert (result / "agent.py").exists()
        assert reg.exists("ExcelAgent")
        # memory mirror called (hybrid) — best-effort
        assert brain.remember.called  # type: ignore


def test_agent_creator_rejects_existing(tmp_path: Path = None):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        agents_root = td / "agents"
        agents_root.mkdir()
        reg = AgentRegistry(registry_path=td / "registry.json", agents_root=agents_root)
        brain = MagicMock()
        brain.remember = MagicMock(return_value={})
        creator = AgentCreator(brain=brain, registry=reg, agents_root=agents_root)  # type: ignore
        spec = DecisionResult(action="CREATE_AGENT", agent_name="ExcelAgent", reason="x", confidence=0.9)
        asyncio.run(creator.create(spec, always_confirm))
        # second create should raise FileExistsError
        try:
            asyncio.run(creator.create(spec, always_confirm))
            assert False, "should raise FileExistsError"
        except FileExistsError:
            pass


def test_agent_creator_wrong_action_noop():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        agents_root = td / "agents"
        agents_root.mkdir()
        reg = AgentRegistry(registry_path=td / "registry.json", agents_root=agents_root)
        brain = MagicMock()
        creator = AgentCreator(brain=brain, registry=reg, agents_root=agents_root)  # type: ignore
        spec = DecisionResult(action="USE_AGENT", agent_name="Example", reason="x", confidence=0.9)
        result = asyncio.run(creator.create(spec, always_confirm))
        assert result is None


def main():
    # simple runner without pytest
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    ok = 0
    fail = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            ok += 1
        except Exception as exc:
            print(f"FAIL {fn.__name__}: {exc}")
            import traceback

            traceback.print_exc()
            fail += 1
    print(f"\n{ok} passed, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
