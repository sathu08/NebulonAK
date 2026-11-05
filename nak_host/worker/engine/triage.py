"""Decision/triage mixin (laya scoring via the INJECTED decider)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from nak.utils.constant import (
    WORKER_DECISION_ROLE_CHARS,
    WORKER_DECISION_TEXT_CHARS,
    WORKER_MEMORY_SNIPPET_CHARS,
    WORKER_TRIAGE_HISTORY_TURNS,
)


class TriageMixin:
    def decision_enabled(self) -> bool:
        """True when workers.json entry has decision.enabled=true + laya."""
        dec = self.entry.get("decision") or {}
        return bool(dec.get("enabled", False)) and str(dec.get("model", "laya")) == "laya"

    def build_decision_input(self, text: str,
                               session_id: Optional[str] = None) -> Dict[str, Any]:
        """decision_input for nak.decision — NO workers/tools here.

        Workers/tools resolve inside decide_task (None = all from
        workers.json / ToolEngine), so they are deliberately excluded
        to avoid duplication. Only situation/history/intent/role/actions.
        session_id: when set, the shared session transcript is folded
        into history so triage judges with the same context as the
        main call (otherwise the gate is session-blind).
        """
        t = str(text or "").strip()
        hist = "\n".join(
            str(m.get("content", ""))[:WORKER_MEMORY_SNIPPET_CHARS]
            for m in self.history[-WORKER_TRIAGE_HISTORY_TURNS:]
            if isinstance(m, dict) and str(m.get("content", "")).strip()
        )
        if session_id and self._memory_on():
            shared = self._recall_session(session_id)
            if shared:
                hist = (hist + "\n" + str(shared.get("content", ""))).strip()
        return {
            "situation": t[:WORKER_DECISION_TEXT_CHARS],
            "history": hist[:WORKER_DECISION_TEXT_CHARS],
            "user_intent": t[:WORKER_DECISION_TEXT_CHARS],
            "worker_role": self.prompt()[:WORKER_DECISION_ROLE_CHARS],
            "available_actions": ["chat", "clarify", "escalate"],
        }

    def _local_policy_gate(self, triage: Dict[str, Any]) -> Dict[str, Any]:
        """Minimal gate used when no policy_evaluator injected (no imports)."""
        pol = ((self.entry.get("decision") or {}).get("policy")) or {}
        if triage.get("needs_clarification") is True and pol.get(
                "clarify_on_needs_clarification", True):
            return {"action": "clarify", "allowed": False,
                    "reasons": ["needs_clarification"], "policy": pol}
        try:
            risk = float(triage.get("risk", 0))
        except (TypeError, ValueError):
            risk = 0.0
        try:
            max_r = float(pol.get("max_risk", 2.0))
        except (TypeError, ValueError):
            max_r = 2.0
        if pol.get("escalate_on_high_risk", True) and risk >= max_r:
            return {"action": "escalate", "allowed": False,
                    "reasons": [f"risk {risk:.2f} >= {max_r:.2f}"], "policy": pol}
        return {"action": "chat", "allowed": True,
                "reasons": ["within policy"], "policy": pol}

    def _entry_policy(self, res: Dict[str, Any]) -> Dict[str, Any]:
        """Attach this worker's policy verdict; fail closed on evaluator error."""
        entry_pol = ((self.entry.get("decision") or {}).get("policy")) or {}
        try:
            if self._policy_evaluator is not None:
                res["policy"] = self._policy_evaluator(res.get("decision", {}), entry_pol)
            else:
                res["policy"] = self._local_policy_gate(res.get("decision", {}))
        except Exception:
            res["policy"] = {"action": "escalate", "allowed": False,
                             "reasons": ["policy error — escalate (fail-closed)"],
                             "policy": entry_pol}
        return res

    def route(
        self,
        text: str,
        workers: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        engine: Any = None,
        workers_file: Any = None,
        session_id: Optional[str] = None,
        llm: bool = False,
        context: Optional[Dict[str, Any]] = None,
        apply_policy: bool = False,
    ) -> Dict[str, Any]:
        """Reusable, side-effect-free decision call through this worker.

        Unlike triage(), this does NOT run the main chat and does NOT write
        memory/history. It passes the worker's OWN decision block (endpoint,
        model, timeout, questions) to the injected decider, so crafts can
        call it repeatedly (one call per plan step) without sharing another
        worker's transport. Default llm=False -> laya endpoint route, which
        is safe to run as a parallel batch.

        `context`: prebuilt build_decision_input snapshot. Pass the SAME
        snapshot to every call in a batch so concurrent calls never race on
        worker history. None = build from current history/session.
        `apply_policy`: attach this worker's policy verdict (off by default;
        assignment decisions are proposals, approval stays separate).
        """
        if not self.decision_enabled():
            return {"ok": False, "enabled": False, "reason": "decision disabled"}
        if self._decider is None:
            return {"ok": False, "enabled": True,
                    "error": "no decider injected — build via nak/wiring.py make_worker()"}
        try:
            observation = (context if isinstance(context, dict)
                           else self.build_decision_input(text, session_id=session_id))
            res = self._decider(
                str(text or "").strip(),
                observation=observation,
                workers=workers,
                tools=tools,
                llm=bool(llm),
                engine=engine,
                workers_file=workers_file,
                decision_cfg=self.entry.get("decision") or {},
            )
            if not isinstance(res, dict):
                return {"ok": False, "enabled": True, "error": "decider returned non-object"}
            if apply_policy:
                res = self._entry_policy(res)
            return res
        except Exception as exc:
            return {"ok": False, "enabled": True, "error": str(exc)[:200]}

    def triage(
        self,
        text: str,
        workers: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        engine: Any = None,
        workers_file: Any = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run triage via INJECTED decider (llm=True, Brain -> NebulonMD) + policy gate.

        BOUNDARY: never `from nak.decision import ...` here — the decider
        callable arrives via the constructor (see nak/wiring.py).
        Returns decide_task() dict + policy verdict:
        {ok, worker, tool, arguments, decision: {...},
         policy: {action: chat|clarify|escalate, allowed, reasons}}.
        workers/tools=None -> decider resolves all (no duplication).
        session_id: shared session key, folded into the decision input
        so triage sees session context (not session-blind).
        Disabled -> {"ok": False, "enabled": False}.
        No decider injected -> {"ok": False, "error": "no decider injected"}.
        """
        if not self.decision_enabled():
            return {"ok": False, "enabled": False, "reason": "decision disabled"}
        if self._decider is None:
            return {"ok": False, "enabled": True,
                    "error": "no decider injected — build via nak/wiring.py make_worker()"}
        try:
            decision_input = self.build_decision_input(text, session_id=session_id)
            res = self._decider(
                str(text or "").strip(),
                observation=decision_input,
                workers=workers,
                tools=tools,
                llm=True,
                engine=engine,
                workers_file=workers_file,
                decision_cfg=self.entry.get("decision") or {},
            )
            if not isinstance(res, dict):
                return {"ok": False, "enabled": True, "error": "decider returned non-object"}
            return self._entry_policy(res)
        except Exception as exc:  # never break chat()
            return {"ok": False, "enabled": True, "error": str(exc)[:200]}
