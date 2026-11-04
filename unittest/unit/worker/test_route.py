"""worker.route(): reusable decision call with no chat/memory side effects."""
from nak.worker import GenericWorker, list_workers


def test_every_worker_decision_block_enables_parallel_decisions():
    entries = list_workers()
    assert entries
    for entry in entries:
        dec = entry.get("decision") or {}
        assert dec.get("parallel") is True, entry.get("name")
        assert int(dec.get("max_parallel", 0)) >= 1, entry.get("name")
        assert dec.get("endpoint") and dec.get("questions"), entry.get("name")


def test_route_passes_own_decision_block_and_is_side_effect_free():
    seen = {}

    def decider(goal, **kwargs):
        seen["goal"] = goal
        seen["kwargs"] = kwargs
        return {"ok": True, "worker": "aayan", "decision": {"risk": 0}}

    w = GenericWorker("vizhi", decider=decider)
    before_history = list(w.history)
    before_memory = dict(w.entry.get("memory") or {})

    out = w.route("pick a worker", workers=["aayan"],
                  context={"situation": "plan step"}, session_id="s1")

    assert out["ok"] is True and out["worker"] == "aayan"
    assert seen["goal"] == "pick a worker"
    assert seen["kwargs"]["workers"] == ["aayan"]
    assert seen["kwargs"]["observation"] == {"situation": "plan step"}
    assert seen["kwargs"]["decision_cfg"] == w.entry["decision"]
    assert seen["kwargs"]["llm"] is False
    assert "policy" not in out
    assert w.history == before_history
    assert (w.entry.get("memory") or {}) == before_memory


def test_route_reports_disabled_decision_without_calling_decider():
    calls = []
    w = GenericWorker("vizhi", decider=lambda *a, **k: calls.append(1))
    w.entry["decision"] = dict(w.entry["decision"], enabled=False)
    out = w.route("x")
    assert out["ok"] is False and out["enabled"] is False
    assert calls == []


def test_route_applies_policy_only_when_asked():
    w = GenericWorker("vizhi", decider=lambda *a, **k: {"ok": True})
    out = w.route("x", context={}, apply_policy=True)
    assert "policy" in out
