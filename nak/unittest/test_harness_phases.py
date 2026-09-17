"""Offline checks for Phase 3 (observer/events) + Phase 4 (verifier/runtime)."""
import asyncio
import json
import os
import sys
import tempfile

os.environ["NAK_POLICY_TOOL_USE"] = "allow_always"
os.environ["NAK_VERIFY_COMMANDS"] = "python3 -m compileall -q ."

from unittest.mock import MagicMock


def check_observer():
    from nak.harness.observer import inspect

    cases = [
        ('{"ok": true, "returncode": 0, "output": "hi"}', "SUCCESS"),
        ('{"error": "boom"}', "FAILURE"),
        ('{"error": "use denied by policy gate"}', "NEEDS_USER"),
        ('{"ok": false, "timeout": true, "command": "x"}', "TIMEOUT"),
        ('{"error": "timed out after 30s"}', "TIMEOUT"),
        ('{"ok": false, "returncode": 1, "output": "1 failed, 0 passed"}', "FAILURE"),
        ('{"ok": true, "returncode": 0, "output": "3 passed, 1 failed"}', "PARTIAL_SUCCESS"),
        ('{"path": "/a/b", "mode": "create"}', "SUCCESS"),
        ("plain text output with no markers", "SUCCESS"),
    ]
    for payload, want in cases:
        got = inspect(payload)["status"]
        assert got == want, f"{payload[:50]} -> {got}, want {want}"
    print("observer: 9/9 classifications OK")


def check_events():
    from nak.harness.state import create_state
    from nak.harness.events import log_event
    import nak.plugins.tool.workspace as ws

    st = create_state("evt test", session_id="ev1check")
    wsroot = tempfile.mkdtemp(prefix="nak_ws_evt_")
    st.workspace = str(ws.ensure_session_workspace("ev1check", root=wsroot))
    log_event(st, "tool_result", "shell SUCCESS: ok", classification="SUCCESS")
    evlog = ws.session_dir("ev1check", root=wsroot) / "logs" / "events.jsonl"
    assert evlog.exists(), "events.jsonl missing"
    print("events jsonl OK:", evlog)


def check_edit_aliases():
    import json as _json
    from pathlib import Path
    from nak.plugins.tool.file_tool import execute_fs_tool

    td = tempfile.mkdtemp(prefix="nak_ws_alias_")
    (Path(td) / "a.py").write_text("x = 1\n", encoding="utf-8")
    # LLM-style paraphrased keys
    out = _json.loads(execute_fs_tool(
        "edit_file", {"file": "a.py", "old": "x = 1", "new": "x = 2"}, root=td))
    assert out.get("replacements") == 1, out
    assert "x = 2" in (Path(td) / "a.py").read_text(encoding="utf-8")
    # canonical keys still win when both present
    out = _json.loads(execute_fs_tool(
        "edit_file", {"path": "a.py", "old_text": "x = 2", "old": "WRONG",
                       "new_text": "x = 3", "new": "WRONG"}, root=td))
    assert out.get("replacements") == 1, out
    assert "x = 3" in (Path(td) / "a.py").read_text(encoding="utf-8")
    print("edit_file aliases OK")


def check_read_write_aliases():
    import json as _json
    from pathlib import Path
    from nak.plugins.tool.file_tool import execute_file_tool
    from nak.plugins.tool.workspace import set_current_workspace

    td = tempfile.mkdtemp(prefix="nak_ws_rwalias_")
    with set_current_workspace(td):
        # LLM-style `file` key (the live T2 miss: read_file {"file": ...})
        out = _json.loads(execute_file_tool(
            "write_file", {"file": "b.py", "content": "y = 1\n"}))
        assert out.get("path") or out.get("written") or "error" not in out, out
        out = _json.loads(execute_file_tool("read_file", {"file": "b.py"}))
        assert "y = 1" in _json.dumps(out, ensure_ascii=False), out
        out = _json.loads(execute_file_tool("read_file", {"filepath": "b.py"}))
        assert "y = 1" in _json.dumps(out, ensure_ascii=False), out
        # canonical `path` still wins when both present
        out = _json.loads(execute_file_tool(
            "read_file", {"path": "b.py", "file": "WRONG.py"}))
        assert "y = 1" in _json.dumps(out, ensure_ascii=False), out
    print("read/write_file aliases OK")


def check_verifier():
    from nak.harness.verifier import Verifier

    td = tempfile.mkdtemp()
    with open(os.path.join(td, "good.py"), "w") as f:
        f.write("x = 1\n")
    r = Verifier(td, checks=[{"name": "compileall",
                              "command": "python3 -m compileall -q " + td}]).verify()
    assert r["ok"] is True, r
    print("verifier pass-case OK; log:", r.get("log"))
    with open(os.path.join(td, "bad.py"), "w") as f:
        f.write("def broken(:\n")
    r2 = Verifier(td, checks=[{"name": "compileall",
                               "command": "python3 -m compileall -q " + td}]).verify()
    assert r2["ok"] is False and r2["failures"] == ["compileall"], r2
    print("verifier fail-case OK")


class SeqBrain:
    def __init__(self, answers):
        self._a = list(answers)

    def chat(self, text, messages=None, session_id=None, **k):
        a = self._a.pop(0) if len(self._a) > 1 else self._a[0]
        return {"answer": a, "turn": 1}

    def create_session(self, metadata=None):
        return {"session_id": "v1"}

    def search(self, q, top_k=None, expand=False, **k):
        return []

    def remember(self, text, **k):
        return {"memory_id": "x"}


def check_runtime_success():
    from nak.harness import HarnessRuntime

    wsroot = tempfile.mkdtemp(prefix="nak_ws_ok_")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Kepler"
    seq = SeqBrain([
        json.dumps({"action": "USE_AGENT", "agent_name": "Kepler",
                    "reason": "plan", "confidence": 0.95}),
        json.dumps({"tool": "list_files", "arguments": {"directory": "."}}),
        json.dumps({"answer": "plan done"}),
    ])
    rt = HarnessRuntime(brain=seq, registry=reg, auto_session=False,
                        workspace_root=wsroot)
    res = asyncio.run(rt.handle("make a plan"))
    assert res["state"]["status"] == "done", res["state"]["status"]
    assert len(res["verification"]) == 1 and res["verification"][0]["ok"] is True
    assert any(o["kind"] == "verify" for o in res["state"]["observations"])
    assert res["state"]["tool_calls"][0].get("classification") == "SUCCESS"
    print("runtime verify-on-success OK; observations:",
          [o["kind"] for o in res["state"]["observations"]])


def check_runtime_failure():
    from nak.harness import HarnessRuntime

    wsroot = tempfile.mkdtemp(prefix="nak_ws_fail_")
    reg = MagicMock()
    reg.available_agents_str.return_value = "Kepler"
    seq = SeqBrain([
        json.dumps({"action": "USE_AGENT", "agent_name": "Kepler",
                    "reason": "code it", "confidence": 0.95}),
        json.dumps({"tool": "write_file",
                    "arguments": {"path": "broken_mod_check.py",
                                  "content": "def broken(:",
                                  "mode": "create"}}),
        json.dumps({"answer": "wrote it"}),
    ])
    rt = HarnessRuntime(brain=seq, registry=reg, auto_session=False,
                        workspace_root=wsroot)
    res = asyncio.run(rt.handle("write broken code"))
    assert res["state"]["status"] == "failed", res["state"]["status"]
    assert "verification FAILED" in res["answer"]
    # isolation: the broken file must be inside the temp workspace, not the repo
    assert os.path.exists(os.path.join(wsroot, "session_default", "project",
                                        "broken_mod_check.py")), res["state"]["workspace"]
    assert not os.path.exists(os.path.join(os.path.dirname(__file__),
                                            "..", "..", "broken_mod_check.py"))
    print("runtime verify-on-failure OK; status=failed, answer carries note")


def check_pytest_missing_hint():
    import importlib.util
    from nak.plugins.tool.exec_tools import run_test

    real_find_spec = importlib.util.find_spec
    importlib.util.find_spec = lambda name: None  # simulate missing pytest
    try:
        res = run_test(".", cwd=tempfile.mkdtemp(prefix="nak_ws_pytest_"))
    finally:
        importlib.util.find_spec = real_find_spec
    assert res["ok"] is False and res["passed"] is False, res
    assert "pytest is not installed" in res["output"], res
    assert "pip install pytest" in res["output"], res
    print("missing-pytest hint OK")


if __name__ == "__main__":
    check_observer()
    check_events()
    check_edit_aliases()
    check_read_write_aliases()
    check_verifier()
    check_pytest_missing_hint()
    check_runtime_success()
    check_runtime_failure()
    print("ALL PHASE 3+4 CHECKS PASSED")
