"""Tests for nebulonak.cfg [harness] section (env > cfg > defaults).

Run: python -m nak.test.test_harness_config
"""
from __future__ import annotations

import os
import tempfile


def _write_cfg(body: str) -> str:
    fd, path = tempfile.mkstemp(prefix="nak_cfg_", suffix=".cfg")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def test_cfg_harness_values():
    from nak.utils.config import load_config

    path = _write_cfg(
        "[harness]\nworkspace_dir = /tmp/nak_ws_cfg\n"
        "verify_commands = echo hi; echo yo\nverify_timeout = 25\n"
        "max_retries = 4\nmax_delegation = 3\n")
    try:
        cfg = load_config(path)
        assert cfg.harness_workspace_dir == "/tmp/nak_ws_cfg", cfg
        assert cfg.harness_verify_commands == "echo hi; echo yo", cfg
        assert cfg.harness_verify_timeout == 25, cfg
        assert cfg.harness_max_retries == 4, cfg
        assert cfg.harness_max_delegation == 3, cfg
    finally:
        os.unlink(path)


def test_cfg_harness_invalid_falls_back():
    from nak.utils.config import load_config

    path = _write_cfg("[harness]\nmax_retries = bogus\nverify_timeout = 9999\n"
                      "max_delegation = 0\n")
    try:
        cfg = load_config(path)
        assert cfg.harness_max_retries == 2, cfg
        assert cfg.harness_verify_timeout == 300, cfg  # clamped
        assert cfg.harness_max_delegation == 1, cfg  # clamped
    finally:
        os.unlink(path)


def test_env_wins_over_cfg():
    from nak.utils.config import get_max_retries, get_verify_timeout

    os.environ["NAK_MAX_RETRIES"] = "1"
    os.environ["NAK_VERIFY_TIMEOUT"] = "33"
    try:
        assert get_max_retries() == 1
        assert get_verify_timeout() == 33
    finally:
        del os.environ["NAK_MAX_RETRIES"]
        del os.environ["NAK_VERIFY_TIMEOUT"]


def test_repo_cfg_parses():
    from nak.utils.config import load_config

    cfg = load_config()  # repo nebulonak.cfg with [harness]
    assert cfg.harness_verify_timeout == 60, cfg
    assert cfg.harness_max_retries == 2, cfg
    assert cfg.harness_max_delegation == 2, cfg


def test_workspace_dir_from_cfg():
    from nak.plugins.tool.workspace import get_workspace_root

    os.environ["NAK_WORKSPACE"] = tempfile.mkdtemp(prefix="nak_ws_env_")
    try:
        assert get_workspace_root() == __import__("pathlib").Path(
            os.environ["NAK_WORKSPACE"]).resolve()
    finally:
        del os.environ["NAK_WORKSPACE"]


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
