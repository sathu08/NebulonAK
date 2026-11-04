"""Offline checks for nak.brain.retry (transient retry, fast-fail, async twin)."""
import asyncio

from types import SimpleNamespace

from nak.brain.client import BrainError
from nak.brain.retry import (acall_with_retry, call_with_retry, is_transient,
                             probe_mind)


def _timeout_err():
    return BrainError(
        "NebulonMind unreachable at http://localhost:9696/api/NebulonMind: "
        "HTTPConnectionPool(host='localhost', port=9696): Read timed out. "
        "(read timeout=60.0)", status=None)


def test_transient_detection():
    assert is_transient(_timeout_err()) is True
    assert is_transient(BrainError("boom", status=503)) is True
    assert is_transient(BrainError("unauthorized", status=401)) is False
    assert is_transient(BrainError("no key configured", status=400)) is False
    assert is_transient(ValueError("plain")) is False
    print("transient detection OK")


def test_recovers_after_transients():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _timeout_err()
        return {"answer": "ok"}

    res = call_with_retry(flaky, retries=2, backoff_base=0)
    assert res == {"answer": "ok"}, res
    assert len(calls) == 3, calls
    print("recover-after-transients OK")


def test_gives_up_after_budget():
    calls = []

    def down():
        calls.append(1)
        raise _timeout_err()

    try:
        call_with_retry(down, retries=2, backoff_base=0)
    except BrainError:
        pass
    else:
        raise AssertionError("should have raised")
    assert len(calls) == 3, calls  # 1 + 2 retries
    print("gives-up-after-budget OK")


def test_permanent_fails_fast():
    calls = []

    def denied():
        calls.append(1)
        raise BrainError("unauthorized", status=401)

    try:
        call_with_retry(denied, retries=2, backoff_base=0)
    except BrainError:
        pass
    else:
        raise AssertionError("should have raised")
    assert len(calls) == 1, calls
    print("permanent-fails-fast OK")


def test_non_brain_error_passthrough():
    calls = []

    def bad():
        calls.append(1)
        raise ValueError("config broken")

    try:
        call_with_retry(bad, retries=2, backoff_base=0)
    except ValueError:
        pass
    else:
        raise AssertionError("should have raised")
    assert len(calls) == 1, calls
    print("non-brain-passthrough OK")


def test_async_twin():
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise _timeout_err()
        return "async-ok"

    res = asyncio.run(acall_with_retry(flaky, retries=2, backoff_base=0))
    assert res == "async-ok", res
    assert len(calls) == 2, calls
    print("async-twin OK")


def test_probe_down_fails_fast():
    calls = []

    def down():
        calls.append(1)
        raise _timeout_err()

    try:
        call_with_retry(down, retries=2, backoff_base=0, probe=lambda: "down")
    except BrainError as exc:
        assert "health probe" in str(exc), exc
    else:
        raise AssertionError("should have raised")
    assert len(calls) == 1, calls  # no wasted 60s retries
    print("probe-down-fails-fast OK")


def test_probe_up_keeps_retrying():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _timeout_err()
        return "recovered"

    res = call_with_retry(flaky, retries=2, backoff_base=0, probe=lambda: "up")
    assert res == "recovered", res
    assert len(calls) == 3, calls
    print("probe-up-keeps-retrying OK")


def test_probe_async_twin():
    import asyncio as _asyncio

    calls = []

    async def down():
        calls.append(1)
        raise _timeout_err()

    try:
        _asyncio.run(acall_with_retry(down, retries=2, backoff_base=0,
                                      probe=lambda: "backend_down"))
    except BrainError as exc:
        assert "backend" in str(exc).lower(), exc
    else:
        raise AssertionError("should have raised")
    assert len(calls) == 1, calls
    print("probe-async-twin OK")


def test_probe_dead_port_is_down():
    fake = SimpleNamespace(base_url="http://127.0.0.1:9/api/NebulonMind")
    assert probe_mind(fake, timeout=2) == "down"
    assert probe_mind(SimpleNamespace(base_url=""), timeout=1) == "down"
    print("probe-dead-port OK")


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
