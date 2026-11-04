"""Parallel decision batches: order, dedupe, isolation, bounds, timeout."""
import time

from nak.decision import decide_many


def test_empty_batch_returns_empty():
    assert decide_many([], lambda r: {"ok": True}) == []


def test_order_and_per_request_ids_are_preserved():
    calls = []

    def fn(request):
        calls.append(request["goal"])
        return {"ok": True, "worker": request["goal"].upper()}

    out = decide_many([{"id": "a", "goal": "x"}, {"id": "b", "goal": "y"}],
                      fn, max_parallel=2)
    assert [r["id"] for r in out] == ["a", "b"]
    assert [r["worker"] for r in out] == ["X", "Y"]
    assert sorted(calls) == ["x", "y"]


def test_duplicate_requests_run_once_but_return_one_per_input():
    calls = []

    def fn(request):
        calls.append(request)
        return {"ok": True, "worker": "vizhi"}

    out = decide_many([{"id": "1", "key": "same", "goal": "x"},
                       {"id": "2", "key": "same", "goal": "x"}], fn,
                      max_parallel=2)
    assert len(calls) == 1
    assert len(out) == 2
    assert [r["id"] for r in out] == ["1", "2"]
    assert all(r["worker"] == "vizhi" for r in out)


def test_one_failure_does_not_cancel_siblings():
    def fn(request):
        if request["goal"] == "bad":
            raise RuntimeError("boom")
        return {"ok": True, "worker": "aayan"}

    out = decide_many([{"id": "bad", "goal": "bad"},
                       {"id": "good", "goal": "good"}], fn, max_parallel=2)
    by_id = {r["id"]: r for r in out}
    assert by_id["bad"]["ok"] is False
    assert "boom" in by_id["bad"]["error"]
    assert by_id["good"]["ok"] is True


def test_non_object_result_becomes_error():
    out = decide_many([{"id": "x"}], lambda r: "not-a-dict")
    assert out[0]["ok"] is False
    assert "non-object" in out[0]["error"]


def test_batch_timeout_marks_pending_items_without_raising():
    def fn(request):
        time.sleep(0.3)
        return {"ok": True, "worker": "vizhi"}

    started = time.monotonic()
    out = decide_many([{"id": "slow"}], fn, max_parallel=1, timeout=0.05)
    assert time.monotonic() - started < 0.25
    assert out[0]["ok"] is False
    assert "timeout" in out[0]["error"]


def test_max_parallel_one_is_serial_but_still_dedupes():
    seen = []

    def fn(request):
        seen.append(request["id"])
        return {"ok": True}

    out = decide_many([{"id": "a"}, {"id": "b"}], fn, max_parallel=1)
    assert seen == ["a", "b"]
    assert [r["id"] for r in out] == ["a", "b"]
