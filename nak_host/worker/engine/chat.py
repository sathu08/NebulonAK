"""One chat turn: triage -> recall -> brain.chat -> store."""
from __future__ import annotations

from typing import Any, Dict, Optional

from nak_host.utils.constant import (
    WORKER_CHAT_HISTORY_TURNS,
    WORKER_SESSION_PROMOTE,
)


class ChatMixin:
    def _require_mind(self) -> None:
        """Hard gate: no Mind (or Mind without DB) -> worker does nothing.

        Duck-typed so scripted brains in tests keep working: only real
        Brain clients preflight; anything without require_mind is trusted.
        """
        if self.brain is None:
            raise RuntimeError(
                "no brain injected — build via nak_host/wiring.py make_worker() "
                "or GenericWorker(name, brain=...)."
            )
        preflight = getattr(self.brain, "require_mind", None)
        if callable(preflight):
            preflight()

    @staticmethod
    def _with_notice(answer: str, triage_res: Dict[str, Any]) -> str:
        """Prepend the decision-endpoint notice so the user actually sees it.

        triage carries {'notice', 'endpoint_live'} when decision.enabled but
        the scoring URL is down (decider.py). Metadata alone is invisible —
        this one line makes the fallback explicit in the reply.
        """
        note = ""
        if isinstance(triage_res, dict):
            note = str(triage_res.get("notice") or "").strip()
        if note and note not in str(answer or ""):
            return f"[{note}]\n{answer}"
        return answer

    def chat(self, text: str, approve: Optional[Any] = None,
             session_id: Optional[str] = None) -> Dict[str, Any]:
        """Identity prompt + user text -> brain.chat(); memory when enabled.

        When entry decision.enabled=true, LLM triage (Brain -> NebulonMD)
        + policy run FIRST: clarify/escalate -> early answer (no main
        LLM call); chat -> Brain.chat() proceeds with triage attached.
        memory.enabled=false: stateless single Brain call, nothing kept.
        memory.enabled=true: recall past (search) -> answer (chat) ->
          store transcript (save_chat -> mind_chats, incl. LLM turns;
          skipped when session_id is set — Mind auto-saved under the
          session key, single transcript shape) +
          store facts (process_turns: decide -> approve -> remember
          -> mind_truth + mind_semantic).
        approve: optional fn(decision) -> bool (harness policy hook).
        session_id: optional Mind session key (e.g. harness.session_id);
          forwarded to brain.chat so turns share one session transcript.
        Store steps are best-effort and reported under "memory".
        """
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        # decision.enabled=true -> trigger LLM triage + policy BEFORE the main LLM call.
        triage_res: Dict[str, Any] = {}
        if self.decision_enabled():
            triage_res = self.triage(text.strip(), session_id=session_id)
            pol = triage_res.get("policy", {}) if isinstance(triage_res, dict) else {}
            action = str(pol.get("action", "chat")).strip().lower() if isinstance(pol, dict) else "chat"
            if action == "clarify":
                return {"answer": self._with_notice(
                                   ("I need a bit more detail before I act — "
                                    "what exactly should I do?"),
                                   triage_res if isinstance(triage_res, dict) else {}),
                        "worker": self.entry["name"], "type": self.entry["type"],
                        "turns": 0, "steps": [], "acted": False,
                        "triage": triage_res, "memory": {"enabled": self._memory_on()},
                        "raw": {}}
            if action == "escalate":
                why = "; ".join(pol.get("reasons", ["high risk"])) if isinstance(pol, dict) else "high risk"
                return {"answer": self._with_notice(
                                   (f"I've flagged this for review ({why}) — "
                                    "approve before I proceed."),
                                   triage_res if isinstance(triage_res, dict) else {}),
                        "worker": self.entry["name"], "type": self.entry["type"],
                        "turns": 0, "steps": [], "acted": False, "needs_approve": True,
                        "triage": triage_res, "memory": {"enabled": self._memory_on()},
                        "raw": {}}
        self._require_mind()  # Mind down (or Mind without DB) -> raise here, do nothing.
        mem = self._memory_on()
        # Identity goes in as a `tool` message, NOT into `text`: Mind persists
        # `text` as the user turn, so embedding the prompt there bloated the
        # shared transcript and the 400-char session snippet kept boilerplate
        # while dropping the real ask. Mind rejects `system`; `tool` is allowed.
        identity = {"role": "tool", "content": self.prompt()}
        history = ([m for m in self.history if m.get("role") in ("user", "assistant", "tool")][-WORKER_CHAT_HISTORY_TURNS:]
                   if mem else [])
        sid = str(session_id or "").strip() or None
        # Ownership: a Mind session keeps its own transcript, so pass its id
        # through and let Mind save. A harness-style key is unknown to Mind ->
        # pass NO session_id (Mind would otherwise newest-wins clobber the
        # shared doc) and merge client-side below. With P1 promotion on, the
        # harness key is mapped to a real Mind session (ensure_mind_session),
        # so Mind owns the transcript while AK still mirrors the turn into the
        # shared harness doc for cross-worker visibility.
        mind_key = sid
        if mem and sid and WORKER_SESSION_PROMOTE:
            mind_key = self.ensure_mind_session(sid) or sid
        mind_owned = bool(mind_key) and self._mind_owns_session(mind_key)
        send_sid = mind_key if mind_owned else None
        tool_context = self._recall_context(text.strip()) if mem else None
        # Mind prepends its own transcript when the forwarded key IS the
        # shared key; the client block is only needed for the client-owned
        # doc (fallback) or a promoted mirror (mind_key != sid).
        read_doc = mem and sid and (not mind_owned or mind_key != sid)
        session_context = self._recall_session(sid) if read_doc else None
        if session_context:
            history = [session_context] + history
        if tool_context:
            history = [tool_context] + history
        res = None
        messages = [identity] + history
        try:
            res = self.brain.chat(text.strip(), messages=messages, session_id=send_sid)
        except TypeError:
            # Older custom brains without session_id support — plain call.
            res = self.brain.chat(text.strip(), messages=messages)
        ans = self._with_notice(
            self._enforce_autonomy(str(res.get("answer", ""))),
            triage_res if isinstance(triage_res, dict) else {})
        memory: Dict[str, Any] = {"enabled": mem, "recalled": bool(tool_context),
                                   "shared": bool(session_context)}
        if mem:
            self.history += [{"role": "user", "content": text.strip()}]
            if ans.strip():
                self.history.append({"role": "assistant", "content": ans.strip()})
            memory.update(self._store_memory(text.strip(), ans, approve=approve,
                                             session_id=session_id,
                                             mind_owned=mind_owned and mind_key == sid))
        out: Dict[str, Any] = {"answer": ans, "worker": self.entry["name"],
                "type": self.entry["type"],
                "turns": 1, "steps": [], "memory": memory, "raw": res}
        if triage_res:
            out["triage"] = triage_res
        return out
