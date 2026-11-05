"""Parallel decision batches — ordered, deduped, bounded, never raises.

Pure stdlib. The caller injects ``fn(request) -> result``; this module does
not import worker/brain/harness and does not know what a decision is. That
keeps nak.decision's boundary intact while letting craft runners issue one
decision per plan step concurrently.

Guarantees:
  * output list matches input order/length (duplicates included);
  * identical requests run once per batch (canonical key or caller key);
  * one failed/timed-out call becomes ``{"ok": False, "error": ...}`` and
    never cancels its siblings;
  * concurrency is capped, so a large plan cannot fan out unbounded;
  * ``timeout`` is a whole-batch budget, not per call.
"""

from __future__ import annotations

import hashlib
import json
import time

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from nak.utils.constant import DECISION_BATCH_TIMEOUT, DECISION_MAX_PARALLEL


def _request_id(request: dict[str, Any], index: int) -> str:
    raw = request.get("id")
    return str(raw).strip() if raw is not None and str(raw).strip() else f"request-{index}"


def _request_key(request: dict[str, Any], index: int) -> str:
    raw = request.get("key")
    if raw is not None and str(raw).strip():
        return f"key:{str(raw).strip()}"
    try:
        blob = json.dumps(request, sort_keys=True, default=str)
    except Exception:
        blob = str(request)
    return "hash:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _error(request: dict[str, Any], index: int, error: str) -> dict[str, Any]:
    return {"ok": False, "id": _request_id(request, index), "error": error[:300]}


def decide_many(
    requests: Iterable[dict[str, Any]],
    fn: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_parallel: int | None = None,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Run ``fn`` over requests, preserving order and isolating failures.

    ``fn`` must be side-effect free and thread-safe (worker.route qualifies:
    it does not mutate history/memory). Non-dict requests become per-item
    errors. ``max_parallel <= 1`` runs serially. The returned list always has
    one dict per input request, each carrying its request ``id``.
    """
    items = [r if isinstance(r, dict) else {} for r in (requests or [])]
    if not items:
        return []
    try:
        cap = int(max_parallel if max_parallel is not None else DECISION_MAX_PARALLEL)
    except (TypeError, ValueError):
        cap = DECISION_MAX_PARALLEL
    cap = max(1, cap)
    try:
        budget = float(timeout if timeout is not None else DECISION_BATCH_TIMEOUT)
    except (TypeError, ValueError):
        budget = DECISION_BATCH_TIMEOUT
    budget = max(0.001, budget)

    keys = [_request_key(item, i) for i, item in enumerate(items)]
    order: list[str] = []
    groups: dict[str, list[int]] = {}
    for i, key in enumerate(keys):
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)
    unique = [(key, groups[key][0]) for key in order]

    def _call(item: dict[str, Any], index: int) -> dict[str, Any]:
        try:
            result = fn(item)
        except Exception as exc:  # one decision never kills the batch
            return _error(item, index, str(exc))
        if not isinstance(result, dict):
            return _error(item, index, "decision returned non-object")
        out = dict(result)
        out.setdefault("id", _request_id(item, index))
        return out

    results: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + budget
    if cap == 1 or len(unique) == 1:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            for key, index in unique:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    results[key] = _error(items[index], index,
                                          "decision batch timeout")
                    continue
                try:
                    results[key] = pool.submit(
                        _call, items[index], index).result(timeout=remaining)
                except Exception as exc:
                    results[key] = _error(items[index], index,
                                          f"decision timeout/failure: {exc}")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    else:
        pool = ThreadPoolExecutor(max_workers=min(cap, len(unique)))
        futures = {key: pool.submit(_call, items[index], index)
                   for key, index in unique}
        try:
            for key, index in unique:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    results[key] = _error(items[index], index,
                                          "decision batch timeout")
                    continue
                try:
                    results[key] = futures[key].result(timeout=remaining)
                except Exception as exc:
                    results[key] = _error(items[index], index,
                                          f"decision timeout/failure: {exc}")
        finally:
            for fut in futures.values():
                fut.cancel()
            pool.shutdown(wait=False, cancel_futures=True)

    out: list[dict[str, Any]] = []
    for i, key in enumerate(keys):
        result = dict(results[key])
        result["id"] = _request_id(items[i], i)
        out.append(result)
    return out
