"""Memory + shared-session persistence mixin."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nak.utils.constant import (
    WORKER_MEMORY_SNIPPET_CHARS,
    WORKER_RECALL_TOP_K,
    WORKER_SESSION_CONTEXT_CHARS,
    WORKER_SESSION_DOC_MAX_TURNS,
    WORKER_SESSION_SAVE_BACKOFF,
    WORKER_SESSION_SAVE_RETRIES,
    WORKER_SESSION_SNIPPET_CHARS,
    WORKER_SESSION_TITLE_CHARS,
    WORKER_SESSION_TURNS,
)

from .session import (
    _session_merge_guard,
    _session_turns,
    _strip_prompt_dump,
)


class MemoryMixin:
    def _memory_on(self) -> bool:
        """memory.enabled flag: False = stateless, True = recall + store."""
        mem = self.entry.get("memory") or {}
        v = mem.get("enabled", True)
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "no", "off", "")
        return bool(v)

    @staticmethod
    def _transcript_id(entry_name: str, when=None) -> str:
        """One chat doc per worker per day in mind_chats (upsert by id)."""
        day = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
        return f"{entry_name}-{day}"

    def _recall_context(self, text: str) -> Optional[Dict[str, str]]:
        """Relevant past memories (mind_truth + mind_semantic, ranked by Mind)."""
        if self.brain is None:
            return None  # no brain injected — grounding skipped, never fatal
        try:
            hits = self.brain.search(text.strip(), top_k=WORKER_RECALL_TOP_K)
        except Exception:
            return None  # grounding is best-effort, never fatal
        bits = []
        for h in hits[:WORKER_RECALL_TOP_K] if isinstance(hits, list) else []:
            c = (h.get("content") or {}) if isinstance(h, dict) else {}
            t = c.get("text", "") if isinstance(c, dict) else str(h)
            if str(t).strip():
                bits.append(str(t)[:WORKER_MEMORY_SNIPPET_CHARS])
        if not bits:
            return None
        return {"role": "tool",
                "content": "things you remember about the user:\n- " + "\n- ".join(bits)}

    def _recall_session(self, session_id: Optional[str]) -> Optional[Dict[str, str]]:
        """Session transcript turns for continuity (best-effort, never fatal).

        Reads the shared Mind transcript for this session key so a worker
        sees what teammates (or its own earlier turns) said. Legacy docs
        stored the rendered identity prompt as the user turn, so those are
        reduced to the real utterance (``_strip_prompt_dump``) BEFORE the
        snippet cap — otherwise the cap keeps boilerplate and drops the ask.
        Only user/assistant roles are kept. Turns are taken most-recent-first
        under a total ``WORKER_SESSION_CONTEXT_CHARS`` budget so a long-lived
        doc cannot blow up the prompt (oldest turns drop, newest survive),
        then restored to chronological order. Returns a tool-context block or
        None when unavailable/empty. Old brains without get_chat are skipped
        (duck-typed, never raises).
        """
        sid = str(session_id or "").strip()
        if not sid or self.brain is None:
            return None
        fetch = getattr(self.brain, "get_chat", None)
        if not callable(fetch):
            return None
        try:
            doc = fetch(sid)
        except Exception:
            return None  # grounding is best-effort, never fatal
        budget = WORKER_SESSION_CONTEXT_CHARS
        used = 0
        kept: List[str] = []
        for m in reversed(_session_turns(doc)[-WORKER_SESSION_TURNS:]):
            text = m["content"]
            if m["role"] == "user":
                text = _strip_prompt_dump(text)
            if not text:
                continue
            line = f"[{m['role']}] {text[:WORKER_SESSION_SNIPPET_CHARS]}"
            if budget > 0 and kept and used + len(line) > budget:
                break
            kept.append(line)
            used += len(line)
        if not kept:
            return None
        kept.reverse()
        return {"role": "tool",
                "content": f"session {sid} so far:\n" + "\n".join(kept)}

    def team_memory(self, session_id: str) -> Dict[str, Any]:
        """Read the shared team memory for a session (leader-visible).

        Thin, documented accessor over ``session_report`` so the planner /
        team leader can read what every teammate wrote to the shared
        transcript after dispatch. Read-only and best-effort.
        """
        return self.session_report(session_id)

    def session_report(self, session_id: str) -> Dict[str, Any]:
        """Read-only view of a shared session (visibility for debugging).

        Mind's GET /agent/session returns only metadata (``message_count``,
        no transcript), so the shared cross-worker transcript lives in the
        ``/chats`` doc keyed by the session id. This surfaces that doc plus
        ownership, so operators can see exactly what teammates share and what
        block AK would inject — without a live call. Best-effort, never
        raises: an unreadable/missing doc yields an ``error`` and empty turns.
        """
        sid = str(session_id or "").strip()
        report: Dict[str, Any] = {
            "session_id": sid,
            "mind_owned": False,
            "message_count": 0,
            "turns": [],
            "injected": None,
        }
        if not sid:
            report["error"] = "empty session id"
            return report
        if self.brain is None:
            report["error"] = "no brain"
            return report
        try:
            report["mind_owned"] = self._mind_owns_session(sid)
        except Exception:
            report["mind_owned"] = False
        fetch = getattr(self.brain, "get_chat", None)
        if callable(fetch):
            try:
                turns = _session_turns(fetch(sid))
                report["message_count"] = len(turns)
                report["turns"] = [
                    {"role": m["role"], "chars": len(m["content"]),
                     "preview": m["content"][:WORKER_SESSION_TITLE_CHARS]}
                    for m in turns
                ]
            except Exception as exc:  # missing doc is normal, not fatal
                report["error"] = str(exc)[:200]
        block = self._recall_session(sid)
        report["injected"] = block.get("content") if isinstance(block, dict) else None
        report["injected_chars"] = len(report["injected"] or "")
        report["context_budget"] = WORKER_SESSION_CONTEXT_CHARS
        return report

    def _list_mind_sessions(self) -> List[Dict[str, Any]]:
        """Active Mind sessions for this user (best-effort, [] on failure)."""
        fn = getattr(self.brain, "list_sessions", None)
        if not callable(fn):
            return []
        try:
            data = fn()
        except Exception:
            return []
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    def ensure_mind_session(self, session_id: str) -> Optional[str]:
        """P1: return a live Mind session id mapped to this harness key.

        Mind generates session ids (it cannot be pinned to the harness key),
        so the mapping is carried in session ``metadata.harness_session`` and
        rediscovered via ``list_sessions`` — every worker sharing the key then
        lands on the SAME Mind session and Mind owns the running transcript.
        Creates one when missing. Returns None when the brain lacks
        create_session/list_sessions (caller falls back to client-owned merge).
        Best-effort, never raises.
        """
        sid = str(session_id or "").strip()
        if not sid or self.brain is None:
            return None
        if self._mind_owns_session(sid):  # harness key is itself a Mind session
            return sid
        for s in self._list_mind_sessions():
            meta = s.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            if str(meta.get("harness_session", "")).strip() != sid:
                continue
            if str(s.get("status", "active")).strip().lower() != "active":
                continue
            mid = str(s.get("session_id", "") or "").strip()
            if mid:
                return mid
        create = getattr(self.brain, "create_session", None)
        if not callable(create):
            return None
        try:
            created = create(metadata={"harness_session": sid,
                                       "source": "nebulonak"})
        except Exception:
            return None
        mid = (created or {}).get("session_id") if isinstance(created, dict) else None
        return str(mid).strip() or None if mid else None

    def _mind_owns_session(self, session_id: Optional[str]) -> bool:
        """True when Mind itself has a live session under this key.

        Ownership decides who writes the transcript. Mind only auto-saves
        (and reads) sessions it created; AK's harness-derived keys are
        unknown to it, so for those Mind saves a 2-message doc and
        newest-wins DROPS every earlier turn. In that case the client must
        own the merge (and must NOT pass session_id, or Mind clobbers the
        doc before the merge runs). Old brains without get_session ->
        client-owned.
        """
        sid = str(session_id or "").strip()
        if not sid or self.brain is None:
            return False
        fetch = getattr(self.brain, "get_session", None)
        if not callable(fetch):
            return False
        try:
            data = fetch(sid)
        except Exception:
            return False
        body = data.get("session") if isinstance(data, dict) else None
        body = body if isinstance(body, dict) else data
        if isinstance(body, dict):
            if "status" in body:
                return str(body.get("status", "")).strip().lower() == "active"
            return bool(body.get("session_id"))
        return bool(body)

    def _merge_session_chat(self, session_id: str, user_text: str,
                            answer: str) -> Dict[str, Any]:
        """Append this turn to the shared session doc (read-modify-write).

        Mind auto-saves API chats only for sessions it knows about; AK uses
        harness-derived keys that Mind treats as stateless, so the client
        owns the merge here. The newest-wins doc store means a naive save of
        just the current turn would DROP earlier teammates' turns — hence
        fetch -> append -> save under a per-session lock (thread + best-effort
        cross-process flock).

        Only when this exact turn is ALREADY the doc's trailing turn do we
        skip (that is the Mind-already-saved case). Earlier duplicates are
        kept: a legitimately repeated utterance must not silently vanish —
        Mind's own identical-turn collapsing is a defect we do not copy.
        The doc is trimmed to ``WORKER_SESSION_DOC_MAX_TURNS`` (oldest first)
        so it cannot grow without bound.

        After saving, the doc is read back and the merge is retried when the
        new turns are not visible yet (``WORKER_SESSION_SAVE_RETRIES``, with
        ``WORKER_SESSION_SAVE_BACKOFF``). This closes the last lost-update
        window: Mind may briefly serve a stale read after a write, so a
        teammate's next merge could otherwise overwrite the just-saved turn.
        """
        info: Dict[str, Any] = {"session_id": session_id}
        fetch = getattr(self.brain, "get_chat", None)
        save = getattr(self.brain, "save_chat", None)
        if not callable(fetch) or not callable(save):
            info["merged"] = False
            return info
        fresh = [{"role": "user", "content": str(user_text).strip()}]
        if str(answer or "").strip():
            fresh.append({"role": "assistant", "content": str(answer).strip()})
        attempts = max(0, int(WORKER_SESSION_SAVE_RETRIES)) + 1
        backoff = max(0.0, float(WORKER_SESSION_SAVE_BACKOFF))
        merged_len = 0
        saved = False
        verified = False
        used = 0
        with _session_merge_guard(session_id):
            for attempt in range(attempts):
                used = attempt + 1
                try:
                    existing = _session_turns(fetch(session_id))
                except Exception:
                    existing = []  # no doc yet (or unreadable) -> start one
                if fresh and existing[-len(fresh):] == fresh:
                    merged_len = len(existing)
                    verified = True
                    break
                merged = existing + fresh
                if (WORKER_SESSION_DOC_MAX_TURNS > 0
                        and len(merged) > WORKER_SESSION_DOC_MAX_TURNS):
                    merged = merged[-WORKER_SESSION_DOC_MAX_TURNS:]
                first_user = next((m["content"] for m in merged
                                   if m["role"] == "user"), "")
                title = (first_user[:WORKER_SESSION_TITLE_CHARS] or "session")
                try:
                    save({"id": session_id, "title": title, "messages": merged})
                except Exception:
                    merged_len = len(existing)
                    if attempt + 1 < attempts and backoff:
                        time.sleep(backoff)
                    continue
                saved = True
                merged_len = len(merged)
                if attempt + 1 >= attempts:
                    break
                try:
                    check = _session_turns(fetch(session_id))
                except Exception:
                    # Read-back unavailable (e.g. a write-only chat store):
                    # retrying cannot tell stale from unreadable, so do not
                    # duplicate the write. Report unverified instead.
                    break
                if check[-len(fresh):] == fresh:
                    merged_len = len(check)
                    verified = True
                    break
                if backoff:
                    time.sleep(backoff)
        info["merged"] = saved
        info["verified"] = verified
        info["attempts"] = used
        info["chat_id"] = session_id
        info["session_turns"] = merged_len
        return info

    def _store_memory(self, user_text: str, answer: str,
                        approve: Optional[Any] = None,
                        session_id: Optional[str] = None,
                        mind_owned: bool = False) -> Dict[str, Any]:
        """Persist the turn (best-effort, never breaks the answer).

        * Session turns (session_id set) are MERGED into the shared doc
          ``<session_id>`` (read -> append this turn -> save) unless Mind
          owns the session (``mind_owned``): then Mind already appended and
          saved this turn, and a client save would be a no-op/duplicate.
        * Without a session, the full in-memory transcript goes to
          ``mind_chats`` via POST /chats (one doc per agent per day, upsert).
        * Facts go to ``mind_truth`` + ``mind_semantic`` via the explicit
          pipeline below: decide (no storage) -> keep should_remember ->
          approve hook -> remember each (server gate still dedupes).
        """
        info: Dict[str, Any] = {"chat_id": None, "decisions": 0,
                                "stored": 0, "skipped": 0}
        sid = str(session_id or "").strip() or None
        transcript = ([{"role": m["role"], "content": m["content"]}
                       for m in self.history
                       if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()])
        if sid and mind_owned:
            info["session_id"] = sid
            info["merged"] = False
            info["session_turns"] = None  # Mind owns the transcript
        elif sid:
            info.update(self._merge_session_chat(sid, user_text, answer))
        elif transcript:
            chat_id = self._transcript_id(self.entry["name"])
            try:
                self.brain.save_chat({
                    "id": chat_id,
                    "title": f"{self.entry['name']} — {chat_id}",
                    "messages": transcript,
                })
                info["chat_id"] = chat_id  # mind_chats
            except Exception:
                pass
        turns = ([{"role": "user", "content": user_text}]
                 + ([{"role": "assistant", "content": answer}] if answer.strip() else []))
        try:
            result = self.process_turns(turns, approve=approve)
            info.update({k: result[k] for k in ("decisions", "stored", "skipped") if k in result})
        except Exception:
            pass
        return info

    def decide(self, turns: str | List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """What is worth remembering? brain.decide = Mind's extractor, NO storage.

        Returns the decision list as-is:
          [{"candidate": {"text","summary","category","importance",...},
            "should_remember": true/false, "reason": ...}, ...]
        """
        self._require_mind()
        try:
            data = self.brain.decide(turns)
        except Exception:
            return []
        if isinstance(data, dict):
            decisions = data.get("decisions", [])
            return decisions if isinstance(decisions, list) else []
        return data if isinstance(data, list) else []

    def store_decisions(self, decisions: List[Dict[str, Any]],
                        approve: Optional[Any] = None) -> Dict[str, Any]:
        """Store accepted decisions one by one (mind_truth + mind_semantic).

        Keeps should_remember=true (default true when absent), asks the
        approve hook (future harness passes it; None = store all kept),
        writes via brain.remember (server lifecycle gate dedupes).
        Best-effort per fact; returns {stored, skipped, errors}.
        """
        stored, skipped, errors = 0, 0, []
        for d in decisions if isinstance(decisions, list) else []:
            if not isinstance(d, dict):
                skipped += 1
                continue
            if d.get("should_remember", True) is False:
                skipped += 1
                continue
            if approve is not None:
                try:
                    if not approve(d):
                        skipped += 1
                        continue
                except Exception as exc:  # noqa: BLE001 — approver crash = skip
                    skipped += 1
                    errors.append(str(exc)[:200])
                    continue
            cand = d.get("candidate") or {}
            text = str(cand.get("text", "") or d.get("text", "") or "").strip()
            if not text:
                skipped += 1
                continue
            kwargs: Dict[str, Any] = {"lang": str(self.identity.get("language", "en") or "en")}
            if cand.get("category"):
                kwargs["category"] = str(cand["category"])
            try:
                importance = float(cand.get("importance", 0) or 0)
                if importance > 0:
                    kwargs["importance"] = importance
            except (TypeError, ValueError):
                pass
            try:
                self.brain.remember(text, **kwargs)
                stored += 1
            except Exception as exc:  # noqa: BLE001 — one bad fact never blocks rest
                errors.append(str(exc)[:200])
        result: Dict[str, Any] = {"stored": stored, "skipped": skipped}
        if errors:
            result["errors"] = errors
        return result

    def process_turns(self, turns: str | List[Dict[str, str]],
                      approve: Optional[Any] = None) -> Dict[str, Any]:
        """Full idea in one call: decide (preview) -> store accepted.

        approve: optional fn(decision) -> bool (harness will pass policy here).
        Returns {decisions, stored, skipped[, errors]}.
        (Gated via decide(); no separate preflight needed.)
        """
        decisions = self.decide(turns)
        result = self.store_decisions(decisions, approve=approve)
        result["decisions"] = len(decisions)
        return result
