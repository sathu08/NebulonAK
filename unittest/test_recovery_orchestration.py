"""Tests for Phase 5 (recovery) + Phase 6 (orchestration). Offline, no Mind.

Run: python -m nak.test.test_recovery_orchestration
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

os.environ["NAK_POLICY_TOOL_USE"] = "allow_always"
os.environ["NAK_VERIFY_COMMANDS"] = "python3 -m compileall -q ."

from unittest.mock import MagicMock

from nak.harness import HarnessRuntime, create_state
from nak.harness.executor import AgentExecutor
from nak.harness.recovery import decide_strategy, max_retries


class SeqBrain:
    """Scripted chat() answers in order (last repeats)."""

    def __init__(self, answers):
        self._a = list(answers)

    def chat(self, text, messages=None, session_id=None, **k):
        a = self._a.pop(0) if len(self._a) > 1 else self._a[0]
        return {"answer": a, "turn": 1}

    def create_session(self, metadata=None):
        return {"session_id": "t56"}

    def search(self, q, top_k=None, expand=False, **k):
        return []

    def remember(self, text, **k):
        return {"memory_id": "x"}


def _use_decision(agent):
    return json.dumps({"action": "USE_AGENT", "agent_name": agent,
                       "reason": "test", "confidence": 0.95})


# -- Phase 5: strategy ------------------------------------------------------


def test_recovery_strategy_escalate_on_budget():
    os.environ["NAK_MAX_RETRIES"] = "1"
    st = create_state("x")
    st.recovery_attempts = 1
    st.verification_results.append({"ok": False, "failures": ["pytest"], "log": ""})
    assert decide_strategy(st)["strategy"] == "escalate"
    del os.environ["NAK_MAX_RETRIES"]


def test_recovery_strategy_escalate_on_denied():
    st = create_state("x")
    st.verification_results.append({"ok": False, "failures": ["pytest"], "log": ""})
    st.record_tool("shell", {}, ok=False, denied=True)
    assert decide_strategy(st)["strategy"] == "escalate"


def test_recovery_strategy_retry_on_timeout():
    st = create_state("x")
    st.verification_results.append({"ok": False, "failures": ["pytest"], "log": ""})
    st.record_error("shell timed out after 30s")
    assert decide_strategy(st)["strategy"] == "retry"


def test_recovery_strategy_replan_on_failure():
    st = create_state("x")
    st.verification_results.append({"ok": False, "failures": ["compileall"], "log": ""})
    assert decide_strategy(st)["strategy"] == "replan"


def test_max_retries_default_and_clamp():
    os.environ.pop("NAK_MAX_RETRIES", None)
    assert max_retries() == 2
    os.environ["NAK_MAX_RETRIES"] = "bogus"
    assert max_retries() == 2
    os.environ["NAK_MAX_RETRIES"] = "99"
    assert max_retries() == 5
    os.environ.pop("NAK_MAX_RETRIES", None)


# -- Phase 5: runtime loop --------------------------------------------------


def test_runtime_recover_then_pass():
    os.environ["NAK_MAX_RETRIES"] = "2"
    reg = MagicMock()
    reg.available_agents_str.return_value = "Kepler"
    seq = SeqBrain([
        _use_decision("Kepler"),
        json.dumps({"tool": "write_file", "arguments": {
            "path": "mod_loop.py", "content": "def broken(:", "mode": "create"}}),
        json.dumps({"answer": "wrote it"}),
        json.dumps({"tool": "write_file", "arguments": {
            "path": "mod_loop.py", "content": "def fixed():\n    return 1\n",
            "mode": "overwrite"}}),
        json.dumps({"answer": "fixed it"}),
    ])
    wsroot = tempfile.mkdtemp(prefix="nak_ws_p5a_")
    rt = HarnessRuntime(brain=seq, registry=reg, auto_session=False,
                        workspace_root=wsroot)
    res = asyncio.run(rt.handle("write a module"))
    assert res["state"]["status"] == "done", res["state"]["status"]
    assert res.get("recovered") is True
    assert res["state"]["recovery_attempts"] == 1
    assert [v["ok"] for v in res["verification"]] == [False, True]
    assert "recovery" in [o["kind"] for o in res["state"]["observations"]]
    os.environ.pop("NAK_MAX_RETRIES", None)


def test_runtime_budget_exhaustion():
    os.environ["NAK_MAX_RETRIES"] = "1"
    reg = MagicMock()
    reg.available_agents_str.return_value = "Kepler"
    answers = [_use_decision("Kepler")]
    for i in range(5):
        answers.append(json.dumps({"tool": "write_file", "arguments": {
            "path": f"bad{i}.py", "content": "def broken(:", "mode": "create"}}))
        answers.append(json.dumps({"answer": f"attempt {i}"}))
    wsroot = tempfile.mkdtemp(prefix="nak_ws_p5b_")
    rt = HarnessRuntime(brain=SeqBrain(answers), registry=reg,
                        auto_session=False, workspace_root=wsroot)
    res = asyncio.run(rt.handle("write broken things"))
    assert res["state"]["status"] == "failed"
    assert res["state"]["recovery_attempts"] == 1
    assert "recovery attempt(s) tried" in res["answer"]
    os.environ.pop("NAK_MAX_RETRIES", None)


# -- Phase 6: agents + registry ---------------------------------------------


def test_new_agents_resolvable_and_registered():
    brain = MagicMock()
    ex = AgentExecutor(brain)
    for name in ("Apollo", "Pulsar", "Astra"):
        assert ex.is_resolvable(name), name
    from nak.utils.agent_registry import AgentRegistry

    reg = AgentRegistry()
    names = {m.name for m in reg.list_agents()}
    for name in ("Apollo", "Pulsar", "Astra",
                 "Kepler", "ResearchAgent", "Polaris"):
        assert name in names, name


def test_decision_prompt_lists_new_agents():
    from nak.prompts import render_prompt
    from nak.utils.agent_registry import AgentRegistry

    prompt = render_prompt("polaris",
                           available_agents=AgentRegistry().available_agents_str(),
                           user_request="write code")
    for name in ("Apollo", "Pulsar", "Astra"):
        assert name in prompt, name


def test_decision_prompt_stays_generic():
    """polaris.md is FROZEN to generic principles: per-agent routing knowledge
    lives in registry.json triggers + the harness pre-filter, never in prose.
    This test fails if anyone adds agent-specific routing lines back."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1]
            / "prompts" / "polaris.md").read_text(encoding="utf-8")
    for forbidden in ("DISAMBIGUATION", "-> Pulsar", "-> Astra",
                      "-> Apollo", "-> Kepler", "-> ResearchAgent"):
        assert forbidden not in text, f"per-agent rule leaked into polaris.md: {forbidden}"
    for required in ("USE_AGENT", "CREATE_AGENT", "ASK_USER",
                     "generic reusable agents", "{available_agents}"):
        assert required in text, required


def test_tool_count_includes_orchestration():
    from nak.plugins import PLUGIN_TOOLS

    names = {s["function"]["name"] for s in PLUGIN_TOOLS}
    for tool in ("delegate", "create_plan", "update_plan"):
        assert tool in names, tool
    for tool in ("read_url", "download_file"):
        assert tool in names, tool
    assert len(PLUGIN_TOOLS) == 19


def test_run_build_detection_and_errors():
    import json as _json
    from nak.plugins.tool.exec_tools import (
        detect_build_command, execute_exec_tool)

    td = tempfile.mkdtemp(prefix="nak_ws_build_")
    assert detect_build_command(td) is None  # empty dir: clean error, no guess
    out = _json.loads(execute_exec_tool("run_build", {}, cwd=td))
    assert "error" in out and "no build system" in out["error"], out
    with open(os.path.join(td, "Makefile"), "w") as f:
        f.write("all:\n\t@echo built-ok\n")
    assert detect_build_command(td) == "make"
    out = _json.loads(execute_exec_tool(
        "run_build", {"command": "echo built-ok"}, cwd=td))
    assert out.get("ok") is True and "built-ok" in out.get("output", ""), out


# -- Phase 6: delegate + plan tools -----------------------------------------


def test_delegate_offline():
    from nak.plugins.tool.agent_tools import execute_agent_tools
    from nak.plugins.tool.workspace import set_current_workspace

    brain = SeqBrain([
        json.dumps({"tool": "list_files", "arguments": {"directory": "."}}),
        json.dumps({"answer": "delegated done"}),
    ])
    # delegate inherits the ambient turn workspace in production; scope it
    # here so the agent's reference writes stay out of the repo root.
    with set_current_workspace(tempfile.mkdtemp(prefix="nak_ws_del_")):
        out = json.loads(execute_agent_tools(
            brain, "delegate",
            {"agent_name": "Kepler", "task": "list things"}))
    assert out["agent"] == "Kepler"
    assert out["answer"] == "delegated done"


def test_delegate_unknown_agent_errors():
    from nak.plugins.tool.agent_tools import execute_agent_tools

    out = json.loads(execute_agent_tools(
        MagicMock(), "delegate",
        {"agent_name": "NopeAgent", "task": "do it"}))
    assert "error" in out


def test_delegate_depth_guard():
    from nak.plugins.tool import agent_tools

    token = agent_tools._delegation_depth.set(agent_tools.max_delegation_depth())
    try:
        out = json.loads(agent_tools.execute_agent_tools(
            MagicMock(), "delegate",
            {"agent_name": "Kepler", "task": "x"}))
        assert "error" in out and "depth" in out["error"]
    finally:
        agent_tools._delegation_depth.reset(token)


def test_plan_tools_need_active_turn():
    from nak.plugins.tool.agent_tools import execute_agent_tools

    out = json.loads(execute_agent_tools(MagicMock(), "create_plan",
                                         {"steps": ["a"]}))
    assert "error" in out and "no active harness turn" in out["error"]


def test_plan_tools_with_ambient_state():
    from nak.plugins.tool.agent_tools import execute_agent_tools
    from nak.plugins.tool.workspace import (
        set_current_harness_state, current_harness_state)

    st = create_state("plan test")
    assert current_harness_state() is None
    set_current_harness_state(st)
    try:
        assert current_harness_state() is st
        out = json.loads(execute_agent_tools(MagicMock(), "create_plan",
                                             {"steps": ["one", "two"]}))
        assert out["plan"] == ["one", "two"] and st.plan == ["one", "two"]
        out = json.loads(execute_agent_tools(MagicMock(), "update_plan",
                                             {"action": "complete", "index": 0}))
        assert out["plan"][0].startswith("[done]")
        out = json.loads(execute_agent_tools(MagicMock(), "update_plan",
                                             {"action": "append", "step": "three"}))
        assert out["plan"][-1] == "three"
        out = json.loads(execute_agent_tools(MagicMock(), "update_plan",
                                             {"action": "bogus"}))
        assert "error" in out
        out = json.loads(execute_agent_tools(MagicMock(), "update_plan",
                                             {"action": "clear"}))
        assert out["plan"] == []
    finally:
        set_current_harness_state(None)


def test_orchestrated_turn_with_plan():
    """USE Apollo that plans, writes valid code, answers -> done + plan kept."""
    reg = MagicMock()
    reg.available_agents_str.return_value = "Apollo"
    seq = SeqBrain([
        _use_decision("Apollo"),
        json.dumps({"tool": "create_plan", "arguments": {
            "steps": ["write module", "verify"]}}),
        json.dumps({"tool": "write_file", "arguments": {
            "path": "feat.py", "content": "def feat():\n    return 42\n",
            "mode": "create"}}),
        json.dumps({"answer": "implemented feat"}),
    ])
    wsroot = tempfile.mkdtemp(prefix="nak_ws_p6_")
    rt = HarnessRuntime(brain=seq, registry=reg, auto_session=False,
                        workspace_root=wsroot)
    res = asyncio.run(rt.handle("implement feat"))
    assert res["state"]["status"] == "done", res["state"]["status"]
    assert res["state"]["plan"] == ["write module", "verify"]
    assert res["run_on"] == "Apollo"


# -- R3: workspace continuity -----------------------------------------------


def test_workspace_reused_and_context_injected():
    import pathlib

    wsroot = tempfile.mkdtemp(prefix="nak_ws_r3_")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Kepler"
    brain = SeqBrain([_use_decision("Kepler")] * 10)
    rt = HarnessRuntime(brain=brain, registry=reg, auto_session=False,
                        workspace_root=wsroot)
    calls = []

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        calls.append(text)
        if len(calls) == 1:
            (pathlib.Path(state.workspace) / "turn1_mod.py").write_text(
                "x = 1\n", encoding="utf-8")
        return {"answer": f"turn {len(calls)} done", "run_on": agent_name,
                "raw": {}, "steps": []}

    rt.agents.run_text = fake_run  # type: ignore[method-assign]
    res1 = asyncio.run(rt.handle("first task"))
    res2 = asyncio.run(rt.handle("second task"))
    assert res1["state"]["status"] == "done"
    assert res2["state"]["status"] == "done"
    assert res1["workspace"] == res2["workspace"]
    assert os.path.isdir(res1["workspace"])
    assert "Workspace context" not in calls[0]
    assert "turn1_mod.py" in calls[1]
    assert "turn 1 done" in calls[1]


def test_reset_session_starts_fresh_workspace():
    wsroot = tempfile.mkdtemp(prefix="nak_ws_r3b_")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Kepler"

    class CountingBrain(SeqBrain):
        def __init__(self):
            super().__init__([_use_decision("Kepler")] * 10)
            self.n = 0

        def create_session(self, metadata=None):
            self.n += 1
            return {"session_id": f"s{self.n}"}

    rt = HarnessRuntime(brain=CountingBrain(), registry=reg,
                        auto_session=False, workspace_root=wsroot)
    rt.session_id = "s1"
    # NOTE: each handle() mints a per-turn Mind session (prompt hygiene), so
    # the counter advances on turns too; reset_session() still yields a fresh id.
    calls = []

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        calls.append(text)
        return {"answer": "ok", "run_on": agent_name, "raw": {}, "steps": []}

    rt.agents.run_text = fake_run  # type: ignore[method-assign]
    res1 = asyncio.run(rt.handle("task one"))
    assert rt.reset_session() == "s2"  # s1 was turn 1's per-turn session
    res2 = asyncio.run(rt.handle("task two"))
    assert res1["workspace"] != res2["workspace"]
    # fresh session: no leaked context
    assert "Workspace context" not in calls[0]
    assert "Workspace context" not in calls[1]


def test_remember_turn_caps_notes():
    rt = HarnessRuntime(brain=MagicMock(), registry=MagicMock(),
                        auto_session=False)
    st = create_state("x")
    for i in range(5):
        st.finish("done")
        rt._remember_turn(st, "A", f"answer {i}")
    assert len(rt._session_notes["default"]) == 3
    assert rt._session_notes["default"][-1].endswith("answer 4")


def test_parse_tool_with_trailing_commentary():
    from nak.agents._react import parse_step_answer

    tool_json = ('{"tool": "edit_file", "arguments": {"path": "a.py", '
                 '"old_text": "x = {1}", "new_text": "y"}}')
    # trailing commentary, including braces, must not break extraction
    step = parse_step_answer(tool_json + "\nI will now apply this edit {done}.")
    assert step["kind"] == "tool" and step["name"] == "edit_file", step
    assert step["args"]["old_text"] == "x = {1}", step
    # leading text also fine
    step = parse_step_answer('Here you go:\n```json\n' + tool_json + '\n```')
    assert step["kind"] == "tool" and step["name"] == "edit_file", step
    # two JSON objects: first complete one wins
    step = parse_step_answer(tool_json + '\n{"note": "extra"}')
    assert step["kind"] == "tool" and step["args"]["path"] == "a.py", step
    # genuine answers still pass through
    step = parse_step_answer('{"answer": "all done"}')
    assert step["kind"] == "answer" and step["text"] == "all done", step
    step = parse_step_answer("just some prose, no braces at all")
    assert step["kind"] == "answer", step


def test_plan_carried_across_turns():
    wsroot = tempfile.mkdtemp(prefix="nak_ws_r4_")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Kepler"
    brain = SeqBrain([_use_decision("Kepler")] * 10)
    rt = HarnessRuntime(brain=brain, registry=reg, auto_session=False,
                        workspace_root=wsroot)
    seen_plans = []
    seen_tasks = []

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        seen_tasks.append(text)
        seen_plans.append(list(state.plan))
        if len(seen_tasks) == 1:
            state.plan = ["write module", "verify"]
        return {"answer": "ok", "run_on": agent_name, "raw": {}, "steps": []}

    rt.agents.run_text = fake_run  # type: ignore[method-assign]
    asyncio.run(rt.handle("first"))
    asyncio.run(rt.handle("second"))
    assert seen_plans[0] == [], seen_plans
    assert seen_plans[1] == ["write module", "verify"], seen_plans
    assert "write module" in seen_tasks[1]  # plan injected into follow-up


def test_plan_not_leaked_across_sessions():
    reg = MagicMock()
    reg.available_agents_str.return_value = "Kepler"
    brain = SeqBrain([_use_decision("Kepler")] * 10)
    rt = HarnessRuntime(brain=brain, registry=reg, auto_session=False,
                        workspace_root=tempfile.mkdtemp(prefix="nak_ws_r4b_"))
    seen_plans = []

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        seen_plans.append(list(state.plan))
        if len(seen_plans) == 1:
            state.plan = ["secret step"]
        return {"answer": "ok", "run_on": agent_name, "raw": {}, "steps": []}

    rt.agents.run_text = fake_run  # type: ignore[method-assign]
    rt.session_id = "sess-a"
    asyncio.run(rt.handle("task a"))
    rt.session_id = "sess-b"
    asyncio.run(rt.handle("task b"))
    assert seen_plans[1] == [], seen_plans


# -- LLM routing with full registry context -------------------------------
# Design: the LLM always decides with the FULL agent list (names +
# descriptions + capabilities). No code pre-filters, no bypasses.
# Disambiguation lives in registry descriptions (data), never in prose.


def _real_registry():
    from nak.utils.agent_registry import AgentRegistry

    return AgentRegistry()


def test_registry_descriptions_disambiguate():
    by_name = {m.name: m for m in _real_registry().list_agents()}
    assert "Does NOT implement" in by_name["Pulsar"].description
    assert "Apollo" in by_name["Pulsar"].description
    assert "Never modifies" in by_name["Astra"].description
    assert "Apollo" in by_name["Astra"].description
    assert "ANY write" in by_name["Apollo"].description


def test_runtime_llm_decides_with_full_list():
    # The decision's agent_name comes straight from the LLM answer —
    # the runtime adds nothing, filters nothing.
    brain = SeqBrain([json.dumps({"action": "USE_AGENT", "agent_name": "Astra",
                                  "reason": "audit", "confidence": 0.88})])
    rt = HarnessRuntime(brain=brain, registry=_real_registry(),
                        auto_session=False,
                        workspace_root=tempfile.mkdtemp(prefix="nak_ws_rt3_"))
    seen = []

    async def fake_run(agent_name, text, session_id=None, state=None, **k):
        seen.append(agent_name)
        return {"answer": "reviewed", "run_on": agent_name, "raw": {}, "steps": []}

    rt.agents.run_text = fake_run  # type: ignore[method-assign]
    res = asyncio.run(rt.handle("audit this module, change nothing"))
    assert res["decision"]["agent_name"] == "Astra"
    assert "route_source" not in res["decision"]["parameters"]
    assert seen == ["Astra"]


def test_shell_destructive_refused():
    import json as _json
    from nak.plugins.tool.exec_tools import (
        destructive_command_reason, execute_exec_tool)

    for bad in ("rm -rf /", "rm -rf / --no-preserve-root", "rm -rf ~",
                "rm -rf $HOME", "rm -rf /*",
                ":(){ :|:& };:", "mkfs.ext4 /dev/sda1", "dd if=x of=/dev/sda"):
        assert destructive_command_reason(bad), bad
    for legit in ("rm -rf build", "rm -f *.o *.pyc", "make clean",
                  "pytest -q .", "echo hello", "ls -la /tmp",
                  "rm README.md", "grep -r rm foo/"):
        assert destructive_command_reason(legit) is None, legit
    # end-to-end: refused BEFORE execution (nothing runs)
    out = _json.loads(execute_exec_tool("shell", {"command": "rm -rf /"}, cwd="/tmp"))
    assert out.get("ok") is False and "error" in out, out
    assert "refused" in out["error"], out
    # workspace-relative containment: traversal + absolute escapes refused…
    td = tempfile.mkdtemp(prefix="nak_ws_rm_")
    out = _json.loads(execute_exec_tool(
        "shell", {"command": "rm -rf ../escape-target"}, cwd=td))
    assert out.get("ok") is False and "escapes workspace" in out.get("error", ""), out
    out = _json.loads(execute_exec_tool(
        "shell", {"command": "sudo rm -rf /tmp/../"}, cwd=td))
    assert out.get("ok") is False, out
    # …while ordinary project cleanup inside the workspace still runs
    os.makedirs(os.path.join(td, "build"), exist_ok=True)
    out = _json.loads(execute_exec_tool(
        "shell", {"command": "rm -rf build"}, cwd=td))
    assert out.get("ok") is True, out
    assert not os.path.exists(os.path.join(td, "build"))


def test_react_steps_carry_args_preview():
    import json as _json
    import os as _os
    from nak.agents._react import run_react_loop

    answers = [
        _json.dumps({"tool": "shell", "arguments": {"command": "echo hello-arg"}}),
        _json.dumps({"answer": "did it"}),
    ]

    class QB:
        def chat(self, text, messages=None, session_id=None, **k):
            return {"answer": answers.pop(0) if len(answers) > 1 else answers[0]}

    _os.environ["NAK_POLICY_TOOL_USE"] = "allow_always"
    _old_max = _os.environ.get("NAK_POLICY_MAX_TURNS")
    _os.environ["NAK_POLICY_MAX_TURNS"] = "6"
    try:
        data = run_react_loop(brain=QB(), agent_name="Probe",
                              role_hint="probe", text="run it",
                              mind_step=lambda p, m, s: QB().chat(p))
    finally:
        # restore module-level test env (other tests depend on allow_always)
        _os.environ["NAK_POLICY_TOOL_USE"] = "allow_always"
        if _old_max is None:
            _os.environ.pop("NAK_POLICY_MAX_TURNS", None)
        else:
            _os.environ["NAK_POLICY_MAX_TURNS"] = _old_max
    assert data["answer"] == "did it", data
    assert data["steps"][0]["tool"] == "shell"
    assert "echo hello-arg" in data["steps"][0].get("args", ""), data


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
