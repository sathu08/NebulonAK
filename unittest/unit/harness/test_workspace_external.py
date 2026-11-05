"""Generic external workspace support: any base dir works, deny lists hold."""
import json
from pathlib import Path

from nak_host.harness.engine import ExecutionHarness


def test_external_base_allowed(tmp_path):
    base = str(tmp_path / "custom-ws")
    h = ExecutionHarness(worker="arivu", user="t", workspace_root=base,
                         approver=lambda *a, **k: True)
    assert Path(h.session_dir).is_dir()
    assert str(Path(h.session_dir)).startswith(str(Path(base)))
    out = json.loads(h.run("file", {"action": "write", "path": "hello.md",
                                    "content": "hi", "mode": "create"}))
    assert out.get("ok") is True
    back = json.loads(h.run("file", {"action": "read", "path": "hello.md"}))
    assert back.get("ok") is True and "hi" in back.get("content", "")


def test_deny_lists_hold_in_external_base(tmp_path):
    h = ExecutionHarness(worker="arivu", user="t",
                         workspace_root=str(tmp_path / "ext-ws"))
    denied = json.loads(h.run("file", {"action": "read", "path": ".env"}))
    assert denied.get("ok") is False
    denied_git = json.loads(h.run("file", {"action": "read",
                                           "path": ".git/config"}))
    assert denied_git.get("ok") is False
    assert h.inspect("file", {"action": "read", "path": ".env"})["ok"] is False


def test_per_call_root_inside_session_allowed(tmp_workspace):
    h = ExecutionHarness(worker="arivu", user="t",
                         workspace_root=tmp_workspace,
                         approver=lambda *a, **k: True)
    out = json.loads(h.run("file", {"action": "write", "path": "a.md",
                                    "content": "x", "mode": "create",
                                    "root": h.session_dir}))
    assert out.get("ok") is True
