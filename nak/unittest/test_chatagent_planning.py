"""Tests for pipeline/chatagent conditional planning (terminal layer only).

Run: python -m nak.test.test_chatagent_planning

Covers: mode resolution, auto-heuristic matrix, plan parsing, handle()
gating (never skips, always plans, auto splits), unresolvable planner,
and runtime-untouched guarantee (nak/harness has no planning imports).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from unittest.mock import MagicMock


def test_planning_mode_resolution():
    from nak.agents.Polaris.planning import planning_mode

    os.environ.pop("NAK_CHAT_PLANNING", None)
    assert planning_mode() == "auto"
    assert planning_mode("always") == "always"
    assert planning_mode("bogus-explicit") == "auto"
    os.environ["NAK_CHAT_PLANNING"] = "never"
    try:
        assert planning_mode() == "never"
        assert planning_mode("always") == "always"  # explicit wins over env
    finally:
        del os.environ["NAK_CHAT_PLANNING"]
    os.environ["NAK_CHAT_PLANNING"] = "bogus-env"
    try:
        assert planning_mode() == "auto"
    finally:
        del os.environ["NAK_CHAT_PLANNING"]


def test_needs_planning_matrix():
    from nak.agents.Polaris.planning import needs_planning

    assert needs_planning("anything", "never") is False
    assert needs_planning("", "always") is False
    assert needs_planning("do it", "always") is True
    assert needs_planning("do it", "bogus") is False  # invalid mode, not always
    # auto: destructive
    assert needs_planning("delete the old auth tables", "auto") is True
    assert needs_planning("drop the legacy schema", "auto") is True
    # auto: complexity verbs
    assert needs_planning("migrate the auth system to JWT", "auto") is True
    assert needs_planning("refactor billing into modules", "auto") is True
    # auto: multi-step markers
    assert needs_planning("write the module and then run the tests", "auto") is True
    assert needs_planning("phase 1: scaffold, phase 2: wire up", "auto") is True
    # auto: long requests
    assert needs_planning("word " * 41, "auto") is True
    # auto: trivial stays unplanned
    for simple in ("hi", "???", "run the tests", "list my files",
                   "write a python module", "remember I like tea"):
        assert needs_planning(simple, "auto") is False, simple


def test_parse_plan_steps():
    from nak.agents.Polaris.planning import parse_plan_steps

    out = parse_plan_steps("1. survey the code\n2. implement\n- verify\n\nnoise\n")
    assert out == ["survey the code", "implement", "verify", "noise"], out
    assert parse_plan_steps("") == []
    assert parse_plan_steps("???") == ["???"]
    long_plan = "\n".join(f"{i}. step {i}" for i in range(1, 20))
    assert len(parse_plan_steps(long_plan)) == 8  # capped


def _pipe(monkey=None):
    import json as _json
    from pipeline.chatagent.pipeline import ChatAgentPipeline

    class SeqBrain:
        def __init__(self):
            self.n = 0

        def chat(self, text, messages=None, session_id=None, **k):
            return {"answer": _json.dumps(
                {"action": "USE_AGENT", "agent_name": "Apollo",
                 "reason": "test", "confidence": 0.9})}

        def create_session(self, metadata=None):
            self.n += 1
            return {"session_id": f"s{self.n}"}

        def search(self, q, top_k=None, expand=False, **k):
            return []

        def remember(self, text, **k):
            return {"memory_id": "x"}

    reg = MagicMock()
    reg.available_agents_str.return_value = "Apollo"
    wsroot = tempfile.mkdtemp(prefix="nak_ws_pipe_")
    os.environ["NAK_WORKSPACE"] = wsroot
    pipe = ChatAgentPipeline(brain=SeqBrain(), registry=reg,  # type: ignore
                             auto_session=False, planning="never")
    return pipe, wsroot


def test_handle_never_skips_planning():
    pipe, wsroot = _pipe()
    calls = []

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        calls.append(text)
        return {"answer": "ok", "run_on": agent_name, "raw": {}, "steps": []}

    pipe.runtime.agents.run_text = fake_run  # type: ignore[method-assign]
    try:
        res = asyncio.run(pipe.handle("migrate everything and then deploy it"))
    finally:
        del os.environ["NAK_WORKSPACE"]
    assert res["run_on"] == "Apollo"
    assert len(calls) == 1  # execution only, no plan call
    assert pipe.last_plan == []


def test_handle_always_plans_and_injects():
    pipe, wsroot = _pipe()
    pipe.planning = "always"
    calls = []

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        calls.append((agent_name, text))
        if "Do NOT execute" in text:
            return {"answer": "1. survey\n2. implement\n3. verify",
                    "run_on": agent_name, "raw": {}, "steps": []}
        return {"answer": "done", "run_on": agent_name, "raw": {}, "steps": []}

    pipe.runtime.agents.run_text = fake_run  # type: ignore[method-assign]
    try:
        res = asyncio.run(pipe.handle("do the thing"))
    finally:
        del os.environ["NAK_WORKSPACE"]
    assert pipe.last_plan == ["survey", "implement", "verify"], pipe.last_plan
    assert len(calls) == 2  # plan + execution
    assert calls[0][0] == "Kepler"  # plan runs on the planner
    assert "Current plan:" in calls[1][1] and "survey" in calls[1][1]
    assert res["run_on"] == "Apollo"


def test_handle_auto_splits_simple_and_complex():
    pipe, wsroot = _pipe()
    pipe.planning = "auto"
    calls = []

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        calls.append(text)
        if "Do NOT execute" in text:
            return {"answer": "1. only step", "run_on": agent_name,
                    "raw": {}, "steps": []}
        return {"answer": "ok", "run_on": agent_name, "raw": {}, "steps": []}

    pipe.runtime.agents.run_text = fake_run  # type: ignore[method-assign]
    try:
        asyncio.run(pipe.handle("hi there"))
        assert len(calls) == 1 and pipe.last_plan == []
        asyncio.run(pipe.handle("migrate auth to JWT and then update callers"))
        assert len(calls) == 1 + 2 and pipe.last_plan == ["only step"]
    finally:
        del os.environ["NAK_WORKSPACE"]


def test_handle_skips_when_planner_missing():
    pipe, wsroot = _pipe()
    pipe.planning = "always"
    calls = []
    pipe.runtime.agents.is_resolvable = lambda name: (  # type: ignore[method-assign]
        False if name == "Kepler" else True)

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        calls.append(text)
        return {"answer": "ok", "run_on": agent_name, "raw": {}, "steps": []}

    pipe.runtime.agents.run_text = fake_run  # type: ignore[method-assign]
    try:
        res = asyncio.run(pipe.handle("do the thing"))
    finally:
        del os.environ["NAK_WORKSPACE"]
    assert res["run_on"] == "Apollo" and pipe.last_plan == [] and len(calls) == 1


def test_runtime_untouched_by_planning():
    import pathlib
    harness = pathlib.Path(__file__).resolve().parents[1] / "harness"
    for mod in ("runtime.py", "state.py", "executor.py"):
        src = (harness / mod).read_text(encoding="utf-8")
        for symbol in ("needs_planning", "make_plan", "planning_mode",
                       "NAK_CHAT_PLANNING", "last_plan"):
            assert symbol not in src, f"{mod} references {symbol}"


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    ok = fail = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {name}: {exc}")
            import traceback

            traceback.print_exc()
            fail += 1
    print(f"\n{ok} passed, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
