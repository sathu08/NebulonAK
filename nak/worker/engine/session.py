"""Shared-session locks + transcript helpers."""
from __future__ import annotations

import os
import hashlib
import tempfile

import threading
import contextlib
from typing import Any, Dict, List

from nak.utils.constant import (
    WORKER_SESSION_LOCK_DIR,
    WORKER_SESSION_LOCKS_MAX,
)

_SESSION_LOCKS: Dict[str, Any] = {}
_SESSION_LOCKS_GUARD = threading.Lock()

#: Marker separating the rendered identity prompt from the real utterance in
#: legacy session docs written before P0 (prompt was sent as `text`).
_PROMPT_TAIL_MARKER = "\n\nUser says:"


def _session_lock(session_id: str) -> Any:
    """One process-wide merge lock per session key (bounded registry).

    Shared sessions are read-modify-write: two workers appending at the same
    time would otherwise clobber each other. The lock serializes merges in
    this process; cross-process writers still rely on Mind's newest-wins
    doc, so keep one writer per session across processes.
    """
    sid = str(session_id or "").strip()
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(sid)
        if lock is None:
            if len(_SESSION_LOCKS) >= WORKER_SESSION_LOCKS_MAX:
                _SESSION_LOCKS.pop(next(iter(_SESSION_LOCKS)), None)
            lock = threading.Lock()
            _SESSION_LOCKS[sid] = lock
        return lock


@contextlib.contextmanager
def _session_file_lock(session_id: str) -> Any:
    """Advisory cross-process lock for one session key (POSIX flock).

    The in-process ``_session_lock`` only serializes threads; two worker
    *processes* sharing a key would still interleave their read-modify-write
    and lose turns. A best-effort ``flock`` on ``<tmp>/nak-sess-<hash>.lock``
    closes that gap. Set ``WORKER_SESSION_LOCK_DIR="off"`` to disable, and
    platforms without ``fcntl`` degrade to thread-only locking (never fatal).
    """
    cfg = str(WORKER_SESSION_LOCK_DIR or "").strip()
    if cfg.lower() == "off":
        yield
        return
    try:
        import fcntl  # POSIX only
    except Exception:  # pragma: no cover - non-POSIX
        yield
        return
    base = cfg or tempfile.gettempdir()
    fh = None
    try:
        os.makedirs(base, exist_ok=True)
        digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:32]
        fh = open(os.path.join(base, f"nak-sess-{digest}.lock"), "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:  # lock dir unwritable etc. -> best-effort, keep going
        if fh is not None:
            fh.close()
        yield
        return
    try:
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


@contextlib.contextmanager
def _session_merge_guard(session_id: str) -> Any:
    """Thread lock + best-effort cross-process lock around a session merge."""
    with _session_lock(session_id):
        with _session_file_lock(session_id):
            yield


def _strip_prompt_dump(text: str) -> str:
    """Recover the user's real utterance from a legacy prompt-dump turn.

    Old sessions stored ``<identity prompt>\\n\\nUser says: <text>`` as the
    user message. Keep the tail so shared context carries what was actually
    asked instead of 1k+ chars of boilerplate.
    """
    raw = str(text or "")
    marker = _PROMPT_TAIL_MARKER
    idx = raw.rfind(marker)
    if idx < 0:
        return raw
    return raw[idx + len(marker):].strip()


def _session_turns(doc: Any) -> List[Dict[str, str]]:
    """user/assistant turns out of a /chats payload (shape-tolerant)."""
    data = (doc.get("chat") if isinstance(doc, dict) else None) or doc
    msgs = data.get("messages", []) if isinstance(data, dict) else []
    if not isinstance(msgs, list):
        return []
    out: List[Dict[str, str]] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip()
        if role not in ("user", "assistant"):
            continue
        text = str(m.get("content", "")).strip()
        if text:
            out.append({"role": role, "content": text})
    return out


