"""SIMPLE tool tests — file / directory / observe happy paths (sandboxed)."""
import json


def _run(harness, tool, args):
    return json.loads(harness.run(tool, args))


def test_file_write_then_read(harness_vizhi_approved):
    w = _run(harness_vizhi_approved, "file", {"action": "write", "path": "notes.md", "content": "hello"})
    assert w["ok"] is True
    r = _run(harness_vizhi_approved, "file", {"action": "read", "path": "notes.md"})
    assert r["ok"] is True
    assert "hello" in r["content"]


def test_file_list(harness_vizhi_approved):
    _run(harness_vizhi_approved, "file", {"action": "write", "path": "a.txt", "content": "x"})
    out = _run(harness_vizhi_approved, "file", {"action": "list", "path": "."})
    assert out["ok"] is True
    assert any(e["name"] == "a.txt" for e in out["entries"])


def test_directory_create_list_rename(harness_vizhi_approved):
    assert _run(harness_vizhi_approved, "directory", {"action": "create", "path": "proj"})["ok"] is True
    lst = _run(harness_vizhi_approved, "directory", {"action": "list", "path": "."})
    assert any(e["name"] == "proj" for e in lst["entries"])
    rn = _run(harness_vizhi_approved, "directory", {"action": "rename", "path": "proj", "new_path": "proj2"})
    assert rn["ok"] is True


def test_observe_capture(harness_vizhi_approved):
    _run(harness_vizhi_approved, "file", {"action": "write", "path": "obs.txt", "content": "observe me"})
    out = json.loads(harness_vizhi_approved.run("observe", {"action": "capture", "path": "obs.txt"}))
    assert out["ok"] is True
    assert "observe me" in out["content"]


def test_terminal_allowlisted_echo(harness_vizhi_approved):
    import json as j
    out = j.loads(harness_vizhi_approved.run("terminal", {"action": "exec", "command": "echo hello"}))
    # terminal may or may not be permitted by limits, but must return JSON with ok bool
    assert "ok" in out


def test_inspect_ok_shape(harness_vizhi_approved):
    plan = harness_vizhi_approved.inspect("file", {"action": "read", "path": "notes.md"})
    assert plan["ok"] is True
    assert plan["tool"] == "file"
    assert "sandbox" in plan
