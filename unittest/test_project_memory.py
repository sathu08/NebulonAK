"""R15 project memory: namespaced store + bounded recall + kill-switch.

Run: python -m nak.test.test_project_memory (offline, FakeBrain only).
"""
from __future__ import annotations

import os


class FakeBrain:
    def __init__(self, hits=None, fail=False):
        self.remembered = []
        self.searches = []
        self._hits = hits or []
        self._fail = fail

    def remember(self, text, **kwargs):
        if self._fail:
            raise RuntimeError("Mind down")
        self.remembered.append((text, kwargs))
        return {"ok": True}

    def search(self, query, top_k=None, **kwargs):
        self.searches.append(query)
        if self._fail:
            raise RuntimeError("Mind down")
        return list(self._hits)


def _state(sid="sess1", request="build parser", workspace=None):
    from nak.harness.state import create_state

    return create_state(request, session_id=sid, workspace=workspace)


def test_build_namespaced():
    from nak.harness.project_memory import build_memory_text

    t = build_memory_text("Build parser", session_id="sess1",
                          files=["a.py"], agent_name="Apollo",
                          status="done", answer="ok")
    assert "[project sess1]" in t, t
    assert "Apollo" in t, t


def test_session_key_sanitised():
    from nak.harness.project_memory import session_key

    assert session_key(None) == "default"
    assert session_key("a/b c!") == "a_b_c_"


def test_store_and_recall_roundtrip():
    from nak.harness import project_memory as pm

    brain = FakeBrain()
    st = _state("sess1")
    assert pm.store_turn(brain, st, "Apollo", "wrote parser") is True
    assert len(brain.remembered) == 1
    text, kw = brain.remembered[0]
    assert "[project sess1]" in text
    assert kw.get("category") == "harness_project"

    brain2 = FakeBrain(hits=[{"text": text}])
    out = pm.recall_for_turn(brain2, _state("sess1"), "build parser")
    assert "Project memory" in out and "sess1" in out, out


def test_no_cross_session_leak():
    from nak.harness import project_memory as pm

    brain = FakeBrain(hits=[{"text": "[project other] something else"}])
    out = pm.recall_for_turn(brain, _state("sess1"), "build parser")
    assert out == "", out


def test_kill_switch_disables():
    from nak.harness import project_memory as pm

    os.environ["NAK_PROJECT_MEMORY"] = "false"
    try:
        brain = FakeBrain()
        assert pm.is_enabled() is False
        assert pm.store_turn(brain, _state(), "A", "x") is False
        assert brain.remembered == []
        assert pm.recall_for_turn(brain, _state(), "q") == ""
        assert brain.searches == []
    finally:
        del os.environ["NAK_PROJECT_MEMORY"]
    assert pm.is_enabled() is True


def test_mind_down_never_raises():
    from nak.harness import project_memory as pm

    brain = FakeBrain(fail=True)
    assert pm.store_turn(brain, _state(), "A", "x") is False
    assert pm.recall_for_turn(brain, _state(), "q") == ""


def test_runtime_remember_stores_best_effort():
    from nak.harness.runtime import HarnessRuntime

    brain = FakeBrain()
    rt = HarnessRuntime(brain=brain, auto_session=False)
    rt.session_id = "sess1"
    st = _state("sess1")
    st.finish("done")
    rt._remember_turn(st, "Apollo", "done ok")
    assert len(brain.remembered) == 1, brain.remembered
    # recall injected into follow-up context
    brain2 = FakeBrain(hits=[{"text": brain.remembered[0][0]}])
    rt2 = HarnessRuntime(brain=brain2, auto_session=False)
    rt2.session_id = "sess1"
    st2 = _state("sess1")
    ctx = rt2._workspace_context(st2)
    assert "Project memory" in ctx, ctx


def test_recall_bounded():
    from nak.harness import project_memory as pm

    big = "[project sess1] " + "x" * 5000
    brain = FakeBrain(hits=[{"text": big}] * 5)
    out = pm.recall_for_turn(brain, _state("sess1"), "q")
    assert len(out) <= 1200, len(out)


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
