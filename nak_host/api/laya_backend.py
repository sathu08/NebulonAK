"""nak_host.api.laya_backend — laya Router holder for api.

This is the ONLY module in `nak_host.api` allowed to import the `laya`
model lib:
  * decider / worker / routes NEVER import `laya` directly.
  * they call `predict()` here instead.

Usage:
    from nak_host.api.laya_backend import predict, run_laya

    answers = predict("read notes.txt", {"route": {...}})
"""

from __future__ import annotations

import threading
from typing import Any, Dict

_ROUTER: Any = None
_LOCK = threading.RLock()  # re-entrant: predict holds it while loader takes it


def get_laya_router() -> Any:
    """Return the shared laya Router, lazy-loading weights on first call.

    Weights stay resident after first load. Thread-safe, one loader wins.
    """
    global _ROUTER
    if _ROUTER is not None:
        return _ROUTER
    with _LOCK:
        if _ROUTER is not None:
            return _ROUTER
        from laya import Router  # local model lib lives ONLY here in api

        _ROUTER = Router()
        return _ROUTER


def predict(state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
    """Run one laya inference: (state, questions) -> result dict.

    Raises RuntimeError when the scorer returns a non-object so callers
    can fall back deterministically (never swallows).
    """
    # One inference at a time — parallel big forwards OOM on small boxes.
    with _LOCK:
        router = get_laya_router()
        result = router.predict(state, questions)
    if not isinstance(result, dict):
        raise RuntimeError("laya scorer returned non-object")
    return result


def run_laya(state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
    """Def function: run one laya inference via `predict()`.

    This is the extension point — add pre/post-processing, caps,
    logging, alternate scorer here. Raises on bad scorer output
    so callers fall back deterministically.
    """
    if not isinstance(questions, dict) or not questions:
        raise ValueError("'questions' must be a non-empty object")
    result = predict(state, questions)
    if not isinstance(result, dict):
        raise RuntimeError("laya scorer returned non-object")
    return result
