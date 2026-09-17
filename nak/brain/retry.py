"""nak.brain.retry -- retry-with-backoff for transient Mind failures.

One helper for every agent loop (_react, Kepler, ResearchAgent, Polaris):
a slow/hung Mind (read timeouts, connection resets, 5xx) gets a second
chance instead of failing the whole turn. Permanent errors (auth, config,
validation, 4xx) fail fast with no sleep.

    from nak.brain.retry import call_with_retry
    raw = call_with_retry(step_fn, prompt, msgs, sid)   # sync loops

    from nak.brain.retry import acall_with_retry
    raw = await acall_with_retry(chat_coro_fn)          # async (Polaris)

Tuning (env wins, matching repo convention):
    NAK_BRAIN_RETRIES      extra attempts after the first (default 2, 0..5)
    NAK_BRAIN_BACKOFF_BASE base seconds, doubled per attempt (default 2.0)
    NAK_BRAIN_PREFLIGHT_TIMEOUT seconds for the health probe (default 5.0)

Fail-fast probe: pass probe=<callable returning "up"|"backend_down"|"down">
(or probe_mind(brain)) and the first transient failure triggers one cheap
probe instead of two more 60s timeouts. Down Mind fails the turn in ~65s
with a start-the-server message; live-but-slow Mind keeps retrying.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable

from .client import BrainError

logger = logging.getLogger("nak.brain.retry")

DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_BASE = 2.0
DEFAULT_PREFLIGHT_TIMEOUT = 5.0

# substrings (lowercased) that mark a BrainError as worth retrying when the
# status code alone is inconclusive (status None = network/timeout already).
_TRANSIENT_HINTS = (
    "timed out", "timeout", "unreachable", "connection reset",
    "connection aborted", "temporarily", "try again",
    "service unavailable", "bad gateway", "gateway timeout",
)


def _env_retries() -> int:
    try:
        return max(0, min(int(os.environ.get("NAK_BRAIN_RETRIES", DEFAULT_RETRIES)), 5))
    except (ValueError, TypeError):
        return DEFAULT_RETRIES


def _env_backoff() -> float:
    try:
        return max(0.0, min(float(os.environ.get("NAK_BRAIN_BACKOFF_BASE", DEFAULT_BACKOFF_BASE)), 30.0))
    except (ValueError, TypeError):
        return DEFAULT_BACKOFF_BASE


def _env_preflight_timeout() -> float:
    try:
        return max(1.0, min(float(os.environ.get("NAK_BRAIN_PREFLIGHT_TIMEOUT", DEFAULT_PREFLIGHT_TIMEOUT)), 15.0))
    except (ValueError, TypeError):
        return DEFAULT_PREFLIGHT_TIMEOUT


def probe_mind(brain: Any, timeout: float | None = None) -> str:
    """Fast liveness check: "up" | "backend_down" | "down". Never raises.

    GET {base_url}/health/ready with a short timeout (stdlib urllib, no deps):
      2xx            -> "up" (Mind + DB answering)
      503            -> "backend_down" (Mind alive, NebulonDB down behind it)
      timeout/refused/other -> "down" (nothing useful listening)
    """
    import urllib.request
    import urllib.error

    t = _env_preflight_timeout() if timeout is None else max(1.0, min(float(timeout), 15.0))
    base = str(getattr(brain, "base_url", "") or "").rstrip("/")
    if not base:
        return "down"
    url = base + "/health/ready"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=t) as resp:
            code = getattr(resp, "status", 200)
            return "up" if 200 <= code < 300 else "backend_down"
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            return "backend_down"
        return "down" if exc.code in (502, 504) else "backend_down"
    except Exception:
        return "down"


_START_HINT = ("start NebulonDB first (`nebulondb start`), then Mind "
               "(`nebulonmind start`), and re-ask")


def _fail_fast(exc: BrainError, verdict: str) -> BrainError:
    """Wrap the original error with the probe verdict + what to do."""
    if verdict == "backend_down":
        return BrainError(f"{exc} [health probe: Mind UP but backend DOWN — {_START_HINT}]",
                          status=getattr(exc, "status", None))
    return BrainError(f"{exc} [health probe: Mind DOWN — {_START_HINT}]",
                      status=getattr(exc, "status", None))


def is_transient(exc: BaseException) -> bool:
    """True when retrying could help (network/timeout/5xx), False for auth/config/4xx."""
    if not isinstance(exc, BrainError):
        return False  # unknown errors fail fast (same as before)
    status = getattr(exc, "status", None)
    if status is None:
        return True  # network-level: timeouts, DNS, refused-during-hang
    if isinstance(status, int) and status >= 500:
        return True
    text = str(exc).lower()
    return any(h in text for h in _TRANSIENT_HINTS)


def call_with_retry(fn: Callable[..., Any], *args: Any,
                    retries: int | None = None,
                    backoff_base: float | None = None,
                    probe: Callable[[], str] | None = None,
                    **kwargs: Any) -> Any:
    """Call fn(*args, **kwargs); retry transient BrainErrors with backoff.

    Returns fn's value, or raises the last error after 1 + retries attempts.
    Non-BrainError exceptions propagate immediately (unchanged behaviour).
    When `probe` is given, the first transient failure runs one cheap health
    check: Mind down/backend-down fails immediately with a start-the-server
    message instead of burning the remaining 60s timeouts.
    """
    max_retries = _env_retries() if retries is None else max(0, min(int(retries), 5))
    base = _env_backoff() if backoff_base is None else max(0.0, float(backoff_base))
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except BrainError as exc:
            if not is_transient(exc) or attempt >= max_retries:
                raise
            if probe is not None and attempt == 0:
                try:
                    verdict = probe()
                except Exception:
                    verdict = "down"
                if verdict != "up":
                    raise _fail_fast(exc, verdict) from exc
            delay = base * (2 ** attempt)
            logger.debug("Mind transient failure (attempt %d/%d), retrying in %.1fs: %s",
                         attempt + 1, max_retries + 1, delay, exc)
            time.sleep(delay)
            attempt += 1


async def acall_with_retry(fn: Callable[..., Any], *args: Any,
                           retries: int | None = None,
                           backoff_base: float | None = None,
                           probe: Callable[[], str] | None = None,
                           **kwargs: Any) -> Any:
    """Async twin of call_with_retry (for Polaris decide/decide_and_run)."""
    max_retries = _env_retries() if retries is None else max(0, min(int(retries), 5))
    base = _env_backoff() if backoff_base is None else max(0.0, float(backoff_base))
    attempt = 0
    while True:
        try:
            res = fn(*args, **kwargs)
            if asyncio.iscoroutine(res):
                return await res
            return res
        except BrainError as exc:
            if not is_transient(exc) or attempt >= max_retries:
                raise
            if probe is not None and attempt == 0:
                try:
                    maybe = probe()
                    if asyncio.iscoroutine(maybe):
                        verdict = await maybe
                    else:
                        # probe() already returned a plain value; use it.
                        verdict = maybe
                except Exception:
                    verdict = "down"
                if verdict != "up":
                    raise _fail_fast(exc, verdict) from exc
            delay = base * (2 ** attempt)
            logger.debug("Mind transient failure (attempt %d/%d), retrying in %.1fs: %s",
                         attempt + 1, max_retries + 1, delay, exc)
            await asyncio.sleep(delay)
            attempt += 1


__all__ = ["DEFAULT_RETRIES", "DEFAULT_BACKOFF_BASE", "DEFAULT_PREFLIGHT_TIMEOUT",
           "is_transient", "probe_mind", "call_with_retry", "acall_with_retry"]
