"""Tests for Polaris (async, Mind-only) + Genesis confirm gate + AgentRegistry hybrid."""
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

from nak.agents.Polaris.models import DecisionResult
from nak.agents.Polaris.agent import Polaris
from nak.agents.Genesis.agent import Genesis
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


# -- Polaris parse -----------------------------------------------------


def test_decision_agent_parse_strict_json():
    agent = Polaris(brain=MagicMock(), registry=MagicMock())
    raw = json.dumps({"action": "USE_AGENT", "agent_name": "ExcelAgent", "reason": "x", "confidence": 0.94, "parameters": {}})
    r = agent._parse_response(raw)
    assert r.action == "USE_AGENT" and r.agent_name == "ExcelAgent"


def test_decision_agent_parse_markdown_wrapped():
    agent = Polaris(brain=MagicMock(), registry=MagicMock())
    raw = '```json\n{"action":"CREATE_AGENT","agent_name":"PDFReaderAgent","reason":"no pdf agent","confidence":0.91}\n```'
    r = agent._parse_response(raw)
    assert r.action == "CREATE_AGENT" and r.agent_name == "PDFReaderAgent"


def test_decision_agent_parse_invalid_returns_error():
    agent = Polaris(brain=MagicMock(), registry=MagicMock())
    try:
        agent._parse_response("not json at all")
        assert False
    except ValueError:
        pass


# -- Polaris async decide (Mind-only) ----------------------------------


def test_decision_agent_decide_use_agent():
    brain = FakeBrain(json.dumps({"action": "USE_AGENT", "agent_name": "Example", "reason": "example can handle", "confidence": 0.92, "parameters": {}}))
    reg = MagicMock()
    reg.available_agents_str.return_value = "Example — minimal harness"
    agent = Polaris(brain=brain, registry=reg)  # type: ignore

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
    agent = Polaris(brain=brain, registry=reg)  # type: ignore
    result = asyncio.run(agent.decide("Read this Excel and find duplicates"))
    assert result.is_create and result.agent_name == "ExcelAgent"


def test_decision_agent_decide_fallback_ask_on_invalid_json():
    brain = FakeBrain("not a json response!!!")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Example"
    agent = Polaris(brain=brain, registry=reg)  # type: ignore
    result = asyncio.run(agent.decide("ambiguous", strict=False))
    assert result.action == "ASK_USER"
    assert result.confidence == 0.0


def test_decision_agent_strict_raises():
    brain = FakeBrain("bad json")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Example"
    agent = Polaris(brain=brain, registry=reg)  # type: ignore
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
    agent = Polaris(brain=ErrorBrain(), registry=reg)  # type: ignore
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


# -- Genesis confirm gate ----------------------------------------------


def test_agent_creator_needs_confirm(tmp_path: Path = None):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        agents_root = td / "agents"
        agents_root.mkdir()
        reg = AgentRegistry(registry_path=td / "registry.json", agents_root=agents_root)
        brain = FakeBrain(json.dumps({}))  # not used for create except remember mirror
        # patch remember to avoid Mind call
        brain.remember = MagicMock(return_value={"memory_id": "x"})
        creator = Genesis(brain=brain, registry=reg, agents_root=agents_root)  # type: ignore

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
        creator = Genesis(brain=brain, registry=reg, agents_root=agents_root)  # type: ignore
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
        creator = Genesis(brain=brain, registry=reg, agents_root=agents_root)  # type: ignore
        spec = DecisionResult(action="USE_AGENT", agent_name="Example", reason="x", confidence=0.9)
        result = asyncio.run(creator.create(spec, always_confirm))
        assert result is None


# -- shared _react loop: manifest + template + generated agents ---------------

REACT_MODULE = "nak.agents._react"


def test_shared_manifest_names_react():
    import importlib

    manifest = Path(__file__).resolve().parents[1] / "agents" / "shared.json"
    assert manifest.exists(), "nak/agents/shared.json missing"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    comps = {c["name"]: c for c in data.get("shared_components", [])}
    assert "_react" in comps, comps.keys()
    entry = comps["_react"]
    assert entry["module"] == REACT_MODULE
    mod = importlib.import_module(entry["module"])
    for fn in entry["provides"]:
        assert callable(getattr(mod, fn, None)), fn


def test_creator_template_brace_safe():
    import string

    from nak.agents.Genesis.agent import _AGENT_PY_TEMPLATE

    fields = {fname for _, fname, _, _ in string.Formatter().parse(_AGENT_PY_TEMPLATE)
              if fname}
    assert fields <= {"name", "reason", "reason_lower", "react_module"}, fields
    assert {"name", "reason", "reason_lower", "react_module"} <= fields


def test_created_agent_uses_shared_loop():
    import importlib.util
    import os

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        agents_root = td / "agents"
        agents_root.mkdir()
        reg = AgentRegistry(registry_path=td / "registry.json", agents_root=agents_root)
        brain = MagicMock()
        brain.remember = MagicMock(return_value={})
        creator = Genesis(brain=brain, registry=reg, agents_root=agents_root)  # type: ignore
        spec = DecisionResult(action="CREATE_AGENT", agent_name="ProbeAgent",
                              reason="probe tasks", confidence=0.9)
        path = asyncio.run(creator.create(spec, always_confirm))
        assert path is not None
        src = (path / "agent.py").read_text(encoding="utf-8")
        # thin agent: imports the canonical loop, embeds no loop of its own
        assert f"from {REACT_MODULE} import run_react_loop" in src
        assert "for _ in range(max_turns)" not in src
        assert "def _parse_step_answer" not in src

        # import + run offline with a scripted brain (tool -> answer)
        spec_name = "probe_agent_generated"
        mod_spec = importlib.util.spec_from_file_location(
            spec_name, path / "agent.py")
        assert mod_spec and mod_spec.loader
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)  # type: ignore

        answers = [
            json.dumps({"tool": "list_files", "arguments": {"directory": "."}}),
            json.dumps({"answer": "probe done"}),
        ]

        class QueueBrain:
            def chat(self, text, messages=None, session_id=None, **k):
                return {"answer": answers.pop(0) if len(answers) > 1 else answers[0]}

        os.environ["NAK_POLICY_TOOL_USE"] = "allow_always"
        try:
            agent = mod.Agent(brain=QueueBrain())
            assert agent._mind_step is not None  # test seam preserved
            data = agent.run_sync("probe it")
        finally:
            del os.environ["NAK_POLICY_TOOL_USE"]
        assert data["answer"] == "probe done", data
        assert data["agent"] == "ProbeAgent", data
        assert any(s.get("tool") == "list_files" and s.get("ok") for s in data["steps"])


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
