"""LIVE evaluation — worker + tool + permission + craft via real NebulonMD.

Run: uv run --with pytest python -m pytest unittest/live/test_live_mind.py -v -s
Requires: NebulonDB :6969 + NebulonMD :9696 + LLM key. Skips cleanly when down.
Each chat is 15-45s+; total ~3-5 min. Writes probe memories + session files.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import uuid
import pytest

from nak_host.brain import Brain, BrainError


def _brain_or_skip():
    b = Brain()
    try:
        b.require_mind()
    except Exception as exc:
        pytest.skip(f"Mind not ready: {exc}")
    try:
        st = b.llm_status()
        if not st.get("configured"):
            pytest.skip(f"LLM not configured: {st}")
    except BrainError as exc:
        pytest.skip(f"llm_status failed: {exc}")
    return b


@pytest.fixture(scope="module")
def brain():
    return _brain_or_skip()


@pytest.fixture(scope="module")
def live_user(brain):
    u = f"ak_live_{uuid.uuid4().hex[:8]}"
    brain.ensure_user(u)
    return u


def test_live_health_and_llm(brain):
    assert brain.health_live()["service"] == "NebulonMind"
    ready = brain.health_ready()
    assert ready.get("backend") == "up", ready
    llm = brain.llm_status()
    assert llm.get("configured") is True, llm
    print(f"\nLIVE llm model={llm.get('model')} provider={llm.get('provider')}")


def test_live_vizhi_chat_simple(brain, live_user):
    from nak_host.wiring import make_worker
    w = make_worker("vizhi", brain=Brain(user=live_user))
    out = w.chat("Hello, who are you in one sentence?")
    assert out["answer"] and len(out["answer"]) > 10
    assert out["worker"] == "vizhi"
    print(f"\nVIZHI simple answer: {out['answer'][:400]}")
    print(f"triage policy: {(out.get('triage') or {}).get('policy', {}).get('action')}")


def test_live_aayan_plan(brain, live_user):
    from nak_host.wiring import make_worker
    w = make_worker("aayan", brain=Brain(user=live_user))
    out = w.chat("Plan in numbered steps: how to organize a project README?")
    assert out["answer"] and len(out["answer"]) > 20
    steps = w.extract_plan(out["answer"])
    print(f"\nAAYAN answer: {out['answer'][:600]}")
    print(f"extracted steps: {steps}")
    action = _triage_action(out)
    if action != "chat":
        pytest.skip(f"triage short-circuited before planning (action={action})")
    assert len(steps) >= 1


def test_live_arivu_build_plan(brain, live_user):
    from nak_host.wiring import make_worker
    w = make_worker("arivu", brain=Brain(user=live_user))
    assert w.can("build") is True
    out = w.chat("Plan in numbered steps: how to fix a login bug with a test?")
    assert out["answer"] and len(out["answer"]) > 20
    print(f"\nARIVU build answer: {out['answer'][:600]}")


def _triage_action(out):
    pol = ((out.get("triage") or {}).get("policy") or {})
    return str(pol.get("action", "chat")).strip().lower()


def test_live_memory_recall_store(brain, live_user):
    from nak_host.wiring import make_worker
    import time
    bu = Brain(user=live_user)
    marker = f"live-marker-{uuid.uuid4().hex[:6]} loves filter coffee"
    bu.remember(marker, category="preference")
    time.sleep(1.0)
    w = make_worker("vizhi", brain=bu)
    out = w.chat(f"Do you remember what I said about {marker.split()[0]}?")
    assert out["memory"]["enabled"] is True
    print(f"\nMEMORY answer: {out['answer'][:400]}")
    action = _triage_action(out)
    if action != "chat":
        pytest.skip(f"triage short-circuited before memory recall (action={action})")
    assert out["memory"].get("recalled") is True, out["memory"]
    assert out["memory"].get("chat_id"), out["memory"]  # transcript persisted
    print(f"memory info: recalled={out['memory'].get('recalled')} stored={out['memory'].get('stored')}")


def test_live_memory_chat_path_recall_store(brain, live_user):
    """Triage disabled, so this always exercises the real recall+store path."""
    from nak_host.wiring import make_worker
    import time
    bu = Brain(user=live_user)
    marker = f"live-marker-{uuid.uuid4().hex[:6]} loves filter coffee"
    bu.remember(marker, category="preference")
    time.sleep(1.0)
    w = make_worker("vizhi", brain=bu)
    w.entry["decision"] = {"enabled": False}
    out = w.chat(f"Do you remember what I said about {marker.split()[0]}?")
    assert out["memory"]["enabled"] is True
    assert out["memory"].get("recalled") is True, out["memory"]
    assert out["memory"].get("chat_id"), out["memory"]
    print(f"\nMEMORY(chat-path) answer: {out['answer'][:400]}")
    print(f"memory info: recalled={out['memory'].get('recalled')} stored={out['memory'].get('stored')}")


def test_live_parallel_shared_memory(brain, live_user):
    """Two peers dispatch in parallel into one session doc: no turn is lost."""
    import time
    from nak_host.wiring import make_worker, new_session
    from nak_host.worker.engine.dispatch import run_workers
    sid = new_session("live-par")
    codeword = f"CODE-{uuid.uuid4().hex[:4]}"
    workers = {}
    for name in ("vizhi", "aayan", "arivu"):
        w = make_worker(name, brain=Brain(user=live_user))
        w.entry["decision"] = {"enabled": False}
        workers[name] = w
    vizhi = workers["vizhi"]
    vizhi.chat(f"Team codeword is {codeword}. Remember it.", session_id=sid)
    fan = run_workers(
        f"Plan:\n1. State {codeword}. [worker=aayan]\n"
        f"2. Repeat {codeword}. [worker=arivu]",
        ["aayan", "arivu"], goal="codeword",
        workers={"aayan": workers["aayan"], "arivu": workers["arivu"]},
        session_id=sid, parallel=True)
    assert fan["parallel"] is True
    assert all(r["ok"] for r in fan["runs"]), fan["runs"]
    want = 2 + 2 * len(fan["runs"])
    reader = Brain(user=live_user)
    msgs = []
    for _ in range(10):
        doc = reader.get_chat(sid)
        msgs = (doc.get("chat") or doc).get("messages", [])
        if len(msgs) == want:
            break
        time.sleep(0.3)
    assert len(msgs) == want, [m.get("role") for m in msgs]
    report = vizhi.team_memory(sid)
    assert report["message_count"] == want, report
    print(f"\nPARALLEL shared memory: codeword={codeword} turns={len(msgs)}")


def test_live_tool_permission_craft(brain, live_user):
    import json
    from nak_host.wiring import make_worker, make_harness, run_craft
    from nak_host.harness.craft import craft as Craft
    bu = Brain(user=live_user)
    ws = str(REPO_ROOT / "workspace" / f"live-{uuid.uuid4().hex[:6]}")
    h = make_harness(worker="vizhi", workspace_root=ws, workers_file=None,
                     approver=lambda *a, **k: True)
    # tool: approved write then read
    wr = json.loads(h.run("file", {"action": "write", "path": "live-note.txt",
                                   "content": "live probe hello"}))
    assert wr["ok"] is True, wr
    rd = json.loads(h.run("file", {"action": "read", "path": "live-note.txt"}))
    assert rd["ok"] is True and "live probe" in rd["content"]
    # permission: aayan (researcher) cannot write; arivu (developer) asks
    ha = make_harness(worker="aayan", workspace_root=ws)
    denied = ha.inspect("file", {"action": "write", "path": "x.txt", "content": "x"})
    assert denied["ok"] is False
    # craft: expand plan + build
    steps = Craft.expand_craft("plan", {"goal": "live probe", "context": "test"})
    assert len(steps) >= 1
    bsteps = Craft.expand_craft("build", {"goal": "fix bug",
                                          "plan": "approved live probe plan"})
    assert len(bsteps) >= 1
    # run_craft live (uses real LLM chat inside)
    w = make_worker("vizhi", brain=bu)
    res = run_craft("List 2 steps to document a test run", w, h,
                       approver=lambda info: True)
    print(f"\nrun_craft live: {res}")
    assert "planned" in res
