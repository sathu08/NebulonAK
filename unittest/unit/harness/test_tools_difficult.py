"""DIFFICULT tool tests — sandbox escapes, denials, truncation, bad input."""
import json


def _run(harness, tool, args):
    return json.loads(harness.run(tool, args))


def test_dotdot_escape_blocked(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "file", {"action": "read", "path": "../evil.txt"})
    assert out["ok"] is False
    assert "escapes" in out["error"] or "denied" in out["error"] or "resource" in out["error"]


def test_absolute_escape_blocked(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "file", {"action": "read", "path": "/etc/passwd"})
    assert out["ok"] is False


def test_env_file_denied(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "file", {"action": "write", "path": ".env", "content": "x"})
    assert out["ok"] is False
    assert "denied" in out["error"].lower()


def test_key_suffix_denied(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "file", {"action": "write", "path": "id_rsa.key", "content": "x"})
    assert out["ok"] is False


def test_secrets_dir_denied(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "file", {"action": "write", "path": "secrets/a.txt", "content": "x"})
    assert out["ok"] is False


def test_git_dir_denied(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "file", {"action": "read", "path": ".git/config"})
    assert out["ok"] is False


def test_missing_path_error(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "file", {"action": "read", "path": ""})
    assert out["ok"] is False


def test_bad_action_error(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "file", {"action": "explode", "path": "a.txt"})
    assert out["ok"] is False
    assert "bad action" in out["error"]


def test_write_create_mode_refuses_overwrite(harness_vizhi_approved):
    _run(harness_vizhi_approved, "file", {"action": "write", "path": "once.txt", "content": "1"})
    out = _run(harness_vizhi_approved, "file", {"action": "write", "path": "once.txt",
                                       "content": "2", "mode": "create"})
    # engine routes write->write_file with mode create; second create must fail
    # (either at tool level already-exists or permission level — both ok=False)
    assert out["ok"] is False


def test_read_truncates_large_file(harness_vizhi_approved):
    big = "A" * 20000
    _run(harness_vizhi_approved, "file", {"action": "write", "path": "big.txt", "content": big})
    out = _run(harness_vizhi_approved, "file", {"action": "read", "path": "big.txt"})
    assert out["ok"] is True
    assert out.get("truncated") is True
    assert len(out["content"]) < len(big) + 500


def test_observe_rejects_directories(harness_vizhi_approved):
    _run(harness_vizhi_approved, "directory", {"action": "create", "path": "somedir"})
    out = _run(harness_vizhi_approved, "observe", {"action": "capture", "path": "somedir"})
    assert out["ok"] is False


def test_delete_root_refused(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "directory", {"action": "delete", "path": "."})
    assert out["ok"] is False


def test_terminal_blocks_sudo(harness_vizhi_approved):
    out = _run(harness_vizhi_approved, "terminal", {"action": "exec", "command": "sudo rm -rf /"})
    assert out["ok"] is False


def test_rename_never_overwrites(harness_vizhi_approved):
    _run(harness_vizhi_approved, "file", {"action": "write", "path": "s1.txt", "content": "1"})
    _run(harness_vizhi_approved, "file", {"action": "write", "path": "s2.txt", "content": "2"})
    out = _run(harness_vizhi_approved, "directory", {"action": "rename", "path": "s1.txt", "new_path": "s2.txt"})
    # rename of file via directory tool: both inside root, dst exists -> refuse
    assert out["ok"] is False
