"""DIFFICULT worker tests — validation, crafts enforcement, memory, triage, boundary.

Evaluates how the worker behaves under bad manifests, edge crafts,
memory on/off, FakeBrain failures, and strict module boundaries.
crafts is the ONLY source of truth (no autonomy block).
"""
import contextlib
import json
import shutil
from pathlib import Path

import pytest

from nak_host.worker import GenericWorker, list_workers
from nak_host.worker.engine.autonomy import (
    _autonomy_from_crafts,
    _autonomy_lines,
)
from nak_host.worker.engine.prompt import _render_prompt_template


# ---------- manifest validation (fail-loud) ----------

def _write_workers(tmp_path, entries):
    p = tmp_path / "workers.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    # crafts.json must sit next to workers.json for derivation — copy real pins
    real = Path(__file__).resolve().parents[3] / "nak_host" / "manifests" / "crafts.json"
    with contextlib.suppress(OSError):
        shutil.copy(str(real), str(tmp_path / "crafts.json"))
    return str(p)


def _base_entry(name="t1"):
    return {
        "name": name,
        "type": "coordinator",
        "identity": {
            "role": "tester", "personality": ["Calm"], "tone": "flat",
            "formality": "professional", "humor": "none",
            "values": ["Quality"], "do_not": [], "language": "en",
        },
        "memory": {"enabled": True},
        "crafts": ["plan"],
    }


def test_duplicate_names_rejected(tmp_path):
    f = _write_workers(tmp_path, [_base_entry("dup"), _base_entry("dup")])
    with pytest.raises(ValueError, match="duplicate"):
        list_workers(f)


def test_forbidden_keys_rejected(tmp_path):
    e = _base_entry("bad1")
    e["model"] = "gpt-x"  # top-level forbidden (identity check runs first, so use top-level)
    with pytest.raises(ValueError, match="forbidden"):
        list_workers(_write_workers(tmp_path, [e]))


def test_missing_memory_rejected(tmp_path):
    e = _base_entry("bad2")
    del e["memory"]
    with pytest.raises(ValueError, match="memory.enabled"):
        list_workers(_write_workers(tmp_path, [e]))


def test_new_type_accepted_from_json_itself(tmp_path):
    # types live in workers.json itself: a brand-new type loads with no code change
    from nak_host.worker.engine.autonomy import _worker_types
    e = _base_entry("novel")
    e["type"] = "reviewer"
    entries = list_workers(_write_workers(tmp_path, [e]))
    assert entries[0]["type"] == "reviewer"
    assert "reviewer" in _worker_types(_write_workers(tmp_path, [e]))
    w = GenericWorker("novel", workers_file=_write_workers(tmp_path, [e]))
    assert w.show()["type"] == "reviewer"


def test_blank_type_defaults_to_coordinator(tmp_path):
    e = _base_entry("bad3")
    e["type"] = "   "
    # blank strips to default coordinator (no crash)
    entries = list_workers(_write_workers(tmp_path, [e]))
    assert entries[0]["type"] == "coordinator"


def test_craft_modes_come_from_registry(tmp_path):
    import json as _json
    from nak_host.worker.engine.autonomy import _craft_modes
    modes = _craft_modes(None)
    assert "plan" in modes and "build" in modes
    # missing registry -> fallback list, never raises
    assert _craft_modes("/no/such/workers.json") == ["plan", "assign", "build"]
    # new pin needs no code change: tmp registry with extra mode
    reg = {"crafts": [
        {"mode": "plan", "version": "1.0.0", "description": "p",
         "tools": ["file"], "max_steps": 3, "approval": "small",
         "auto_steps": 2, "path": "plan"},
        {"mode": "qux", "version": "1.0.0", "description": "q",
         "tools": ["file"], "max_steps": 2, "approval": "all",
         "auto_steps": 1, "path": "plan"},
    ]}
    wf = tmp_path / "workers.json"
    wf.write_text(_json.dumps([_base_entry("t1")]), encoding="utf-8")
    (tmp_path / "crafts.json").write_text(_json.dumps(reg), encoding="utf-8")
    assert "qux" in _craft_modes(str(wf))
    a = _autonomy_from_crafts(["qux"], str(wf), "t1")
    assert a["modes"] == ["qux"] and a["max_steps"] == 2


def test_step_limits_come_only_from_planning_crafts(tmp_path):
    reg = {"crafts": [
        {"mode": "plan", "version": "1.0.0", "description": "p",
         "tools": ["file"], "max_steps": 3, "approval": "small",
         "auto_steps": 2},
        {"mode": "assign", "version": "1.0.0", "description": "a",
         "tools": ["file"], "max_steps": 99, "approval": "all",
         "auto_steps": 99},
    ]}
    wf = tmp_path / "workers.json"
    wf.write_text(json.dumps([_base_entry("t1")]), encoding="utf-8")
    (tmp_path / "crafts.json").write_text(json.dumps(reg), encoding="utf-8")
    a = _autonomy_from_crafts(["plan", "assign"], str(wf), "t1")
    assert a["max_steps"] == 3
    assert a["auto_steps"] == 2
    assert a["approval"] == "all"


def test_autonomy_honors_nak_crafts_env(tmp_path, monkeypatch):
    reg = {"crafts": [
        {"mode": "plan", "version": "1.0.0", "description": "p",
         "tools": ["file"], "max_steps": 6, "approval": "small",
         "auto_steps": 6},
    ]}
    reg_path = tmp_path / "custom-crafts.json"
    reg_path.write_text(json.dumps(reg), encoding="utf-8")
    wf = tmp_path / "workers.json"
    wf.write_text(json.dumps([_base_entry("t1")]), encoding="utf-8")
    monkeypatch.setenv("NAK_CRAFTS", str(reg_path))
    a = _autonomy_from_crafts(["plan"], str(wf), "t1")
    assert a["max_steps"] == 6
    assert a["auto_steps"] == 6


def test_worker_types_fallback_never_raises():
    from nak_host.worker.engine.autonomy import _worker_types
    assert _worker_types("/no/such/workers.json") == ["coordinator", "researcher", "coder"]
    assert isinstance(_worker_types(None), list) and _worker_types(None)


def test_bad_identity_field_rejected(tmp_path):
    e = _base_entry("bad4")
    e["identity"]["nickname"] = "bob"
    with pytest.raises(ValueError, match="bad identity fields"):
        list_workers(_write_workers(tmp_path, [e]))


def test_bad_decision_type_rejected(tmp_path):
    e = _base_entry("bad5")
    e["decision"] = {"enabled": True, "model": "laya",
                     "questions": [{"name": "q1", "type": "telepathy"}]}
    with pytest.raises(ValueError, match="bad decision type"):
        list_workers(_write_workers(tmp_path, [e]))


def test_crafts_must_be_list(tmp_path):
    e = _base_entry("bad6")
    e["crafts"] = "plan"
    with pytest.raises(ValueError, match="'crafts' must be a list"):
        list_workers(_write_workers(tmp_path, [e]))


def test_empty_prompt_file_rejected(tmp_path):
    e = _base_entry("bad7")
    e["prompt_file"] = "   "
    with pytest.raises(ValueError, match="empty 'prompt_file'"):
        list_workers(_write_workers(tmp_path, [e]))


# ---------- crafts derivation (no autonomy block) ----------

def test_autonomy_key_rejected(tmp_path):
    e = _base_entry("bad_auto")
    e["autonomy"] = {"modes": ["plan"]}
    with pytest.raises(ValueError, match="was removed"):
        list_workers(_write_workers(tmp_path, [e]))


def test_crafts_derivation_defaults(tmp_path):
    from nak_host.utils.constant import DEFAULT_WORKERS_JSON as _DEFAULT_WORKERS
    a = _autonomy_from_crafts(None, str(_DEFAULT_WORKERS), "x")
    assert "plan" in a["modes"]
    assert a["approval"] in ("all", "small", "none")


def test_crafts_unknown_filters_to_assist(tmp_path):
    f = _write_workers(tmp_path, [_base_entry("t1")])
    a = _autonomy_from_crafts(["ghost-craft"], f, "t1")
    assert a["modes"] == ["assist"]


def test_crafts_explicit_empty_is_assist(tmp_path):
    f = _write_workers(tmp_path, [_base_entry("t1")])
    a = _autonomy_from_crafts([], f, "t1")
    assert a["modes"] == ["assist"]


def test_crafts_vizhi_vs_arivu_derivation():
    vizhi = GenericWorker("vizhi")
    aayan = GenericWorker("aayan")
    arivu = GenericWorker("arivu")
    assert vizhi.can("plan") and vizhi.can("assign")
    assert aayan.can("plan") and not aayan.can("assign")
    assert arivu.can("plan") and arivu.can("build") and not arivu.can("assign")
    # assign pin is approval=all (most restrictive) -> vizhi always waits
    assert vizhi.needs_plan_approval(1) is True
    # plan pin is approval=small/auto_steps=2 -> small plans run ahead
    assert aayan.needs_plan_approval(2) is False
    assert aayan.needs_plan_approval(3) is True
    assert arivu.needs_plan_approval(2) is False
    assert arivu.needs_plan_approval(3) is True


def test_needs_plan_approval_all_small_none(tmp_path):
    # vizhi (plan+assign) -> all -> always True
    e = _base_entry("w_all")
    e["crafts"] = ["plan", "assign"]
    w = GenericWorker("w_all", workers_file=_write_workers(tmp_path, [e]))
    assert w.needs_plan_approval(1) is True
    # plan-only -> small/auto_steps=2 -> only over 2 waits
    e2 = _base_entry("w_small")
    e2["crafts"] = ["plan"]
    w2 = GenericWorker("w_small", workers_file=_write_workers(tmp_path, [e, e2]))
    assert w2.needs_plan_approval(2) is False
    assert w2.needs_plan_approval(3) is True
    # explicit [] -> assist -> can(plan) False, approval all -> always True
    e3 = _base_entry("w_assist")
    e3["crafts"] = []
    w3 = GenericWorker("w_assist", workers_file=_write_workers(tmp_path, [e, e2, e3]))
    assert w3.can("plan") is False
    assert w3.needs_plan_approval(8) is True


# ---------- crafts enforcement on model output ----------

def test_enforce_assist_strips_plan_header_and_worker_tags(tmp_path):
    e = _base_entry("assist_w")
    e["crafts"] = []
    w = GenericWorker("assist_w", workers_file=_write_workers(tmp_path, [e]))
    out = w._enforce_autonomy("Plan:\nhello [worker=vizhi] world")
    assert "Plan:" not in out
    assert "[worker=" not in out
    assert "hello" in out


def test_enforce_truncates_beyond_max_steps(tmp_path):
    e = _base_entry("plan_w")
    e["crafts"] = ["plan"]  # real pin max_steps=3
    w = GenericWorker("plan_w", workers_file=_write_workers(tmp_path, [e]))
    ans = "1. one\n2. two\n3. three\n4. four\n5. five"
    out = w._enforce_autonomy(ans)
    assert "1. one" in out and "3. three" in out
    assert "5. five" not in out
    assert "truncated to 3 steps" in out


def test_enforce_assign_strips_tags_when_no_assign(tmp_path):
    e = _base_entry("planonly")
    e["crafts"] = ["plan"]
    w = GenericWorker("planonly", workers_file=_write_workers(tmp_path, [e]))
    out = w._enforce_autonomy("1. step [worker=vizhi] done")
    assert "[worker=" not in out
    assert "done" in out


# ---------- prompt template edge cases ----------

def test_render_drops_empty_labels():
    ident = {"role": "r", "personality": [], "tone": "", "formality": "",
             "humor": "", "language": "", "values": [], "do_not": []}
    out = _render_prompt_template("Personality: {{personality}}\nTone: {{tone}}\nHi {{role}}",
                                  ident, "bob")
    assert "Personality:" not in out
    assert "Tone:" not in out
    assert "Hi r" in out


def test_autonomy_lines_assist_vs_plan():
    assert any("assist" in ln for ln in _autonomy_lines({"modes": ["assist"], "max_steps": 3}))
    lines = _autonomy_lines({"modes": ["plan"], "max_steps": 3, "approval": "all", "auto_steps": 2})
    assert any("propose a numbered plan" in ln for ln in lines)
    assert any("Whenever you describe or propose a plan" in ln for ln in lines)
    assert any("waits for approval" in ln for ln in lines)


# ---------- chat / memory / triage ----------

def test_chat_session_key_client_owned_merges_not_daily(worker_vizhi, fake_brain):
    # A harness session key is unknown to Mind (no get_session) -> AK owns the
    # transcript: session_id is NOT forwarded (Mind would newest-wins clobber
    # the shared doc), and the turn is MERGED under that id, never daily.
    out = worker_vizhi.chat("hello session", session_id="sess_123")
    assert all(c["session_id"] is None for c in fake_brain.chat_calls)
    assert out["memory"]["session_id"] == "sess_123"
    assert [d["id"] for d in fake_brain.saved] == ["sess_123"]
    assert out["memory"]["merged"] is True
    assert out["memory"]["session_turns"] == 2


def test_chat_mind_owned_session_forwards_and_defers(tmp_path):
    """A Mind-created (active) session keeps its own transcript: the key IS
    forwarded and AK defers the save to Mind (no clobber, no duplicate)."""
    from nak_host.worker import GenericWorker
    from conftest import FakeBrain

    class OwnedBrain(FakeBrain):
        def get_session(self, session_id):
            return {"session": {"session_id": session_id, "status": "active"}}

    brain = OwnedBrain()
    e = _base_entry("owned")
    e["crafts"] = []
    w = GenericWorker("owned", brain=brain,
                      workers_file=_write_workers(tmp_path, [e]))
    out = w.chat("hello owned", session_id="mind_sess")
    assert [c["session_id"] for c in brain.chat_calls if c["session_id"]] == ["mind_sess"]
    assert out["memory"]["session_id"] == "mind_sess"
    assert out["memory"]["merged"] is False
    assert brain.saved == []  # Mind owns the save


def test_chat_without_session_keeps_daily_doc(worker_vizhi, fake_brain):
    out = worker_vizhi.chat("hello daily")
    assert "session_id" not in out["memory"]
    assert len(fake_brain.saved) == 1  # daily {worker}-{date} upsert as before


def test_chat_old_brain_without_session_id_still_works(tmp_path):
    from nak_host.worker import GenericWorker

    class OldBrain:
        def require_mind(self):
            pass

        def chat(self, prompt, messages=None):
            return {"answer": "old ok"}

        def search(self, text, top_k=3):
            return []

        def decide(self, turns):
            return {"decisions": []}

        def remember(self, text, **kwargs):
            return {"ok": True}

        def save_chat(self, doc):
            return {"ok": True}

    e = _base_entry("oldsess")
    e["crafts"] = []
    w = GenericWorker("oldsess", brain=OldBrain(),
                      workers_file=_write_workers(tmp_path, [e]))
    out = w.chat("hi", session_id="sess_old")
    assert out["answer"] == "old ok"


# ---------- session sharing (Part B read path) ----------

def test_session_notes_merged_into_prompt(worker_vizhi, fake_brain):
    fake_brain.session_docs["sess_team"] = [
        {"role": "user", "content": "investigate the login bug"},
        {"role": "assistant", "content": "found it: null check missing in app.py"},
    ]
    out = worker_vizhi.chat("continue the work", session_id="sess_team")
    assert out["memory"]["shared"] is True
    main_call = next(c for c in fake_brain.chat_calls
                     if "session sess_team so far" in str(c["messages"]))
    blocks = [m.get("content", "") for m in (main_call["messages"] or [])]
    assert any("session sess_team so far" in b and "null check" in b for b in blocks)


def test_session_missing_or_broken_is_skipped(worker_vizhi, fake_brain):
    out = worker_vizhi.chat("hello", session_id="no-such-session")
    assert out["memory"]["shared"] is False
    assert out["answer"]  # chat still succeeds


def test_session_skipped_when_stateless(tmp_path):
    from conftest import FakeBrain
    brain = FakeBrain(answer="hi")
    brain.session_docs["s1"] = [{"role": "user", "content": "x"}]
    e = _base_entry("quiet")
    e["memory"] = {"enabled": False}
    e["crafts"] = []
    w = GenericWorker("quiet", brain=brain,
                      workers_file=_write_workers(tmp_path, [e]))
    out = w.chat("hi", session_id="s1")
    assert out["memory"]["shared"] is False


def test_session_turns_capped_and_filtered(worker_vizhi, fake_brain):
    msgs = [{"role": "system", "content": "should be dropped"}]
    msgs += [{"role": "user", "content": f"note {i} " + "x" * 2000} for i in range(15)]
    fake_brain.session_docs["big"] = msgs
    out = worker_vizhi.chat("go", session_id="big")
    assert out["memory"]["shared"] is True
    main_call = next(c for c in fake_brain.chat_calls
                     if "session big so far" in str(c["messages"]))
    block = next(m["content"] for m in (main_call["messages"] or [])
                 if "session big so far" in m.get("content", ""))
    assert "system" not in block and "should be dropped" not in block
    assert block.count("[user]") <= 10  # never more than the turn cap
    assert "note 14" in block  # newest turns survive the budget
    assert "note 0" not in block  # oldest dropped once budget is spent


def test_triage_sees_session_context(worker_vizhi, fake_brain):
    fake_brain.session_docs["sess_gate"] = [
        {"role": "user", "content": "codeword marigold"},
    ]
    plain = worker_vizhi.build_decision_input("repeat it")
    assert "marigold" not in plain["history"]
    gated = worker_vizhi.build_decision_input("repeat it", session_id="sess_gate")
    assert "marigold" in gated["history"]
    # missing session -> same as plain, never raises
    assert worker_vizhi.build_decision_input("repeat it", session_id="ghost")["history"] == plain["history"]


def test_chat_requires_nonempty(worker_vizhi):
    with pytest.raises(ValueError, match="non-empty"):
        worker_vizhi.chat("   ")


def test_chat_no_brain_raises(bare_worker_vizhi):
    # vizhi decision.enabled=true but no decider injected -> triage returns
    # no-decider error, then _require_mind raises no-brain error
    with pytest.raises(RuntimeError, match="no brain injected"):
        bare_worker_vizhi.chat("hello")


def test_chat_stateless_when_memory_disabled(tmp_path):
    from conftest import FakeBrain
    e = _base_entry("stateless")
    e["memory"] = {"enabled": False}
    e["crafts"] = []
    if "decision" in e:
        del e["decision"]
    brain = FakeBrain(answer="hi there")
    w = GenericWorker("stateless", brain=brain,
                      workers_file=_write_workers(tmp_path, [e]))
    out = w.chat("hello")
    assert out["answer"] == "hi there"
    assert out["memory"]["enabled"] is False
    assert w.history == []  # nothing kept
    assert brain.saved == [] and brain.remembered == []


def test_chat_memory_recall_and_store(worker_vizhi, fake_brain):
    fake_brain.search_hits = [{"content": {"text": "user likes tea"}}]
    fake_brain.decisions = [{"candidate": {"text": "likes tea", "importance": 1},
                             "should_remember": True}]
    out = worker_vizhi.chat("remember I like tea")
    assert out["memory"]["enabled"] is True
    assert out["memory"]["recalled"] is True
    assert len(worker_vizhi.history) == 2  # user + assistant
    assert len(fake_brain.remembered) >= 1
    assert len(fake_brain.saved) == 1  # transcript upsert


def test_chat_search_failure_never_breaks(tmp_path):
    from conftest import FakeBrain

    class BoomSearch(FakeBrain):
        def search(self, text, top_k=3):
            raise RuntimeError("db down")

    e = _base_entry("resilient")
    e["crafts"] = []
    brain = BoomSearch(answer="ok answer")
    w = GenericWorker("resilient", brain=brain,
                      workers_file=_write_workers(tmp_path, [e]))
    out = w.chat("hi")
    assert out["answer"] == "ok answer"
    assert out["memory"]["recalled"] is False


def test_store_decisions_respects_approve_and_should_remember(tmp_path):
    from conftest import FakeBrain
    e = _base_entry("storer")
    brain = FakeBrain()
    w = GenericWorker("storer", brain=brain,
                      workers_file=_write_workers(tmp_path, [e]))
    decisions = [
        {"candidate": {"text": "keep me", "importance": 2}, "should_remember": True},
        {"candidate": {"text": "drop me"}, "should_remember": False},
        {"candidate": {"text": ""}, "should_remember": True},  # empty -> skip
        "not-a-dict",
    ]
    res = w.store_decisions(decisions, approve=lambda d: "keep" in d["candidate"]["text"])
    assert res["stored"] == 1
    assert res["skipped"] == 3


def test_triage_disabled_when_no_decision(tmp_path):
    from conftest import FakeBrain
    e = _base_entry("nodec")
    e["crafts"] = []
    w = GenericWorker("nodec", brain=FakeBrain(),
                      workers_file=_write_workers(tmp_path, [e]))
    assert w.decision_enabled() is False
    assert w.triage("hi")["enabled"] is False


def test_triage_clarify_and_escalate_paths(worker_vizhi):
    # force clarify via injected policy evaluator
    worker_vizhi._policy_evaluator = lambda triage, pol: {"action": "clarify", "allowed": False,
                                                          "reasons": ["needs more"], "policy": pol}
    worker_vizhi._decider = lambda *a, **k: {"ok": True, "decision": {"needs_clarification": True}}
    out = worker_vizhi.chat("do thing")
    assert "need a bit more detail" in out["answer"]
    assert out["acted"] is False
    # force escalate
    worker_vizhi._policy_evaluator = lambda triage, pol: {"action": "escalate", "allowed": False,
                                                          "reasons": ["risk 3 >= 2"], "policy": pol}
    out2 = worker_vizhi.chat("do risky thing")
    assert out2["needs_approve"] is True


def test_boundary_no_runtime_brain_import():
    import nak_host.worker.engine.worker as mod
    lines = Path(mod.__file__).read_text(encoding="utf-8").splitlines()
    # runtime imports = top-level import/from NOT inside `if TYPE_CHECKING:` block
    runtime = []
    in_tc = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("if TYPE_CHECKING"):
            in_tc = True
            continue
        if in_tc:
            if ln.startswith((" ", "\t")) or not s or s.startswith("#"):
                continue  # still inside the guarded block / blank
            in_tc = False  # dedented -> back to runtime
        if s.startswith(("import ", "from ")):
            runtime.append(s)
    blob = "\n".join(runtime)
    assert "nak_host.brain" not in blob
    assert "nak_host.decision" not in blob
    assert "nak_host.harness" not in blob
