"""Live harness test against running NebulonMind. NOT offline-safe.

Run: NAK_POLICY_TOOL_USE=allow_always NAK_POLICY_CREATE_AGENT=no \
     NAK_TIMEOUT=150 python3 -m nak.test.test_live_harness

Covers: brain health/user/memory/chat, live Polaris routing,
full USE Apollo turn with workspace isolation + verification.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

from nak.brain import Brain


def check_brain_basics(brain: Brain) -> None:
    live = brain.health_live()
    print("mind live:", json.dumps(live, ensure_ascii=False)[:200])
    llm = brain.llm_status()
    print("llm status:", json.dumps(llm, ensure_ascii=False)[:200])
    assert llm.get("configured") or llm.get("data", {}).get("configured"), llm
    me = brain.ensure_user()
    print("user ok:", json.dumps(me, ensure_ascii=False)[:200])
    remembered = brain.remember("Live harness probe: NebulonAK runs on NebulonMind")
    print("remember:", json.dumps(remembered, ensure_ascii=False)[:200])
    hits = brain.search("what runs on NebulonMind")
    print(f"search hits: {len(hits)}")
    reply = brain.chat("Reply with exactly: LIVE-OK")
    print("chat:", json.dumps(reply, ensure_ascii=False)[:300])
    print("BRAIN BASICS OK")


def check_live_routing(brain: Brain):
    from nak.agents.Polaris.agent import Polaris
    from nak.utils.agent_registry import AgentRegistry

    decision = Polaris(brain=brain, registry=AgentRegistry())
    result = asyncio.run(decision.decide(
        "Write a Python module with an add(a, b) function"))
    print("decision:", json.dumps(result.to_dict(), ensure_ascii=False)[:400])
    assert result.action == "USE_AGENT", result.to_dict()
    print("LIVE ROUTING OK ->", result.agent_name)
    return result


def check_live_routing_stages(brain: Brain) -> None:
    """Full-list LLM routing live: the LLM sees every agent (names +
    descriptions + capabilities) and decides; no code pre-filtering."""
    import tempfile
    from nak.harness import HarnessRuntime
    from nak.utils.agent_registry import AgentRegistry

    wsroot = tempfile.mkdtemp(prefix="nak_live_route_")
    rt = HarnessRuntime(brain=brain, registry=AgentRegistry(),
                        auto_session=False, workspace_root=wsroot)
    calls = []
    orig_chat = brain.chat
    brain.chat = lambda *a, **k: (calls.append(1), orig_chat(*a, **k))[1]  # type: ignore
    try:
        st1 = rt.create_state("run the tests please")
        d1 = asyncio.run(rt.decide(st1))
        assert d1.action == "USE_AGENT" and d1.agent_name == "Pulsar", d1.to_dict()
        print("LLM route OK: Pulsar")

        st2 = rt.create_state("write a python script for pandas and dashboard")
        d2 = asyncio.run(rt.decide(st2))
        assert d2.action == "USE_AGENT" and d2.agent_name == "Apollo", d2.to_dict()
        assert len(calls) >= 2, "each decision must consult the LLM"
        print(f"LLM route OK: Apollo ({len(calls)} LLM call(s) total)")
    finally:
        brain.chat = orig_chat  # type: ignore
    print("LIVE ROUTING STAGES OK")


def check_live_turn(brain: Brain) -> None:
    from unittest.mock import MagicMock  # noqa: F401  (kept for symmetry)
    from nak.harness import HarnessRuntime
    from nak.utils.agent_registry import AgentRegistry

    wsroot = tempfile.mkdtemp(prefix="nak_live_")
    rt = HarnessRuntime(brain=brain, registry=AgentRegistry(),
                        auto_session=True, workspace_root=wsroot)
    res = asyncio.run(rt.handle(
        "Write a Python module calc_live with a function add(a, b) "
        "returning their sum. Keep it to one small file."))
    print("run_on:", res.get("run_on"))
    print("status:", res["state"]["status"])
    print("steps:", json.dumps(res.get("steps", []), ensure_ascii=False)[:500])
    print("verification:", json.dumps(res.get("verification", []),
                                      ensure_ascii=False)[:500])
    print("answer:", (res.get("answer") or "")[:800])
    assert res["state"]["status"] == "done", json.dumps(
        res["state"], ensure_ascii=False, default=str)[:2000]
    # isolation: module inside workspace, not repo root
    proj = res["state"]["workspace"]
    assert proj and proj.startswith(wsroot), proj
    import pathlib

    files = [p.name for p in pathlib.Path(proj).rglob("*.py")]
    print("workspace py files:", files)
    assert files, "agent wrote no python file"
    assert not list(pathlib.Path(".").glob("calc_live*")), "repo root polluted"
    print("LIVE TURN OK")


def check_live_testing_turn(brain: Brain) -> None:
    from nak.harness import HarnessRuntime
    from nak.utils.agent_registry import AgentRegistry

    wsroot = tempfile.mkdtemp(prefix="nak_live_test_")
    rt = HarnessRuntime(brain=brain, registry=AgentRegistry(),
                        auto_session=True, workspace_root=wsroot)
    res = asyncio.run(rt.handle(
        "Create a tiny pytest file test_sanity_live.py with one passing "
        "test and run it with run_test."))
    print("testing run_on:", res.get("run_on"))
    print("testing status:", res["state"]["status"])
    print("testing steps:", json.dumps(res.get("steps", []), ensure_ascii=False)[:500])
    print("testing answer:", (res.get("answer") or "")[:500])
    assert res["state"]["status"] == "done", json.dumps(
        res["state"], ensure_ascii=False, default=str)[:2000]
    print("LIVE TESTING TURN OK")


def check_live_continuity(brain: Brain) -> None:
    """R3 live: turn 2 builds on turn 1 files in the SAME session workspace."""
    from nak.harness import HarnessRuntime
    from nak.utils.agent_registry import AgentRegistry
    import pathlib

    wsroot = tempfile.mkdtemp(prefix="nak_live_cont_")
    rt = HarnessRuntime(brain=brain, registry=AgentRegistry(),
                        auto_session=True, workspace_root=wsroot)
    res1 = asyncio.run(rt.handle(
        "Write a Python module mymath with a function add(a, b). One small file."))
    assert res1["state"]["status"] == "done", res1["state"]["status"]
    res2 = None
    for attempt in range(3):  # transient Mind timeouts happen under load; retry
        res2 = asyncio.run(rt.handle(
            "Add a function mul(a, b) returning their product to the module "
            "you just created. Do not create a second module."))
        if not (res2.get("answer") or "").startswith("[brain error"):
            break
        print(f"continuity turn 2 transient brain error, retry {attempt + 1}/3")
    assert res2 is not None
    print("continuity run_on:", res2.get("run_on"))
    print("continuity status:", res2["state"]["status"])
    print("continuity answer:", (res2.get("answer") or "")[:400])
    assert res2["state"]["status"] == "done", json.dumps(
        res2["state"], ensure_ascii=False, default=str)[:2000]
    assert res1["workspace"] == res2["workspace"]
    proj = pathlib.Path(res1["workspace"])
    py_files = sorted(p.name for p in proj.rglob("*.py")
                      if "__pycache__" not in str(p))
    print("continuity workspace py files:", py_files)
    assert py_files == ["mymath.py"], py_files
    content = (proj / "mymath.py").read_text(encoding="utf-8")
    assert "def add" in content and "def mul" in content, (
        f"FILE:\n{content[:500]}\nFULL ANSWER:\n{(res2.get('answer') or '')[:2000]}"
    )
    print("LIVE CONTINUITY OK")


def check_live_recovery(brain: Brain) -> None:
    """R2 live: dictate content with a syntax error -> verify FAILS ->
    recovery fixes it -> verify OK -> recovered."""
    from nak.harness import HarnessRuntime
    from nak.utils.agent_registry import AgentRegistry
    import pathlib

    wsroot = tempfile.mkdtemp(prefix="nak_live_rec_")
    rt = HarnessRuntime(brain=brain, registry=AgentRegistry(),
                        auto_session=True, workspace_root=wsroot)
    res = asyncio.run(rt.handle(
        "Write a Python module quirks with EXACTLY this content, "
        "character for character, do not fix or improve it:\n"
        "def double(n)\n    return n * 2"))
    print("recovery run_on:", res.get("run_on"))
    print("recovery status:", res["state"]["status"])
    print("recovery attempts:", res["state"]["recovery_attempts"])
    print("recovery flag:", res.get("recovered"))
    print("verifications:", [(v["ok"], v["failures"])
                             for v in res.get("verification", [])])
    print("recovery answer:", (res.get("answer") or "")[:600])
    proj = pathlib.Path(res["workspace"])
    src = (proj / "quirks.py").read_text(encoding="utf-8")
    assert res["state"]["status"] == "done", res["state"]["status"]
    assert res.get("recovered") is True
    assert res["state"]["recovery_attempts"] >= 1
    assert "def double(n):" in src, src[:300]
    print("LIVE RECOVERY OK")


def main() -> int:
    if os.environ.get("NAK_LIVE_ONLY") == "continuity":
        check_live_continuity(Brain())
        print("LIVE CONTINUITY PASSED")
        return 0
    if os.environ.get("NAK_LIVE_ONLY") == "recovery":
        check_live_recovery(Brain())
        print("LIVE RECOVERY PASSED")
        return 0
    if os.environ.get("NAK_LIVE_ONLY") == "routing":
        check_live_routing_stages(Brain())
        print("LIVE ROUTING PASSED")
        return 0
    brain = Brain()
    check_brain_basics(brain)
    check_live_routing(brain)
    check_live_turn(brain)
    check_live_testing_turn(brain)
    check_live_continuity(brain)
    print("ALL LIVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
