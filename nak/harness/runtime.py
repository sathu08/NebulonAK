"""nak.harness.runtime -- HarnessRuntime, the central execution layer (Phase 1).

Owns the lifecycle the old pipeline only prototyped:

    request -> create_state -> decide -> route -> execute -> observe -> done

Contract (same as ChatAgentPipeline, plus state):
    USE_AGENT    -> run that agent ONLY if resolvable; missing -> create proposal
    CREATE_AGENT -> NEVER auto-run; proposal with needs_confirm=True (unless auto_create)
    ASK_USER     -> return decision.reason as the clarifying question, no execution

Every turn returns the legacy dict shape {decision, answer, run_on,
routed_via, needs_confirm, created_path, session_id, steps} PLUS
{"state": HarnessState.to_dict()} so callers can inspect/persist the trace.

should_verify / should_recover are intentional stubs (Phase 3/4/5).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from nak.agents.Genesis.agent import Genesis
from nak.agents.Polaris.agent import Polaris
from nak.agents.Polaris.models import DecisionResult
from nak.brain.client import Brain, BrainError
from nak.utils.agent_registry import AgentRegistry

from .executor import AgentExecutor
from .state import (
    HarnessState,
    create_state,
    STATUS_RUNNING,
    STATUS_NEEDS_CONFIRM,
    STATUS_WAITING_USER,
    STATUS_DONE,
    STATUS_FAILED,
)

logger = logging.getLogger("nak.harness.runtime")


class HarnessRuntime:
    """Central harness execution layer."""

    def __init__(
        self,
        brain: Optional[Brain] = None,
        registry: Optional[AgentRegistry] = None,
        decision_agent: Optional[Polaris] = None,
        creator: Optional[Genesis] = None,
        auto_session: bool = True,
        workspace_root: Optional[str | Path] = None,
        instant_route: bool = False,
    ) -> None:
        self.brain = brain or Brain()
        self.registry = registry or AgentRegistry()
        # Token-saving consent mode, passed in (pipeline -> here ->
        # Polaris). An explicitly provided decision_agent keeps its
        # own mode; otherwise the new one inherits this flag.
        self.instant_route = bool(instant_route)
        self.decision = decision_agent or Polaris(
            brain=self.brain, registry=self.registry, instant_route=self.instant_route
        )
        self.creator = creator or Genesis(
            brain=self.brain, registry=self.registry
        )
        self.agents = AgentExecutor(self.brain)
        self._workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root
            else None
        )
        self.session_id: Optional[str] = None
        if auto_session:
            self.session_id = self._open_session()
        self._history: List[Dict[str, str]] = []
        # R3: per-session follow-up memory (session_id or "default" -> summaries
        # of agent-executed turns). Lets turn N build on turn N-1's workspace.
        self._session_notes: Dict[str, List[str]] = {}
        # R4: per-session plan carry-over (session key -> plan steps). Restored
        # into each new turn's state so multi-turn projects keep their plan.
        self._session_plans: Dict[str, List[str]] = {}

    # -- session / workspace ---------------------------------------------

    def _open_session(self) -> Optional[str]:
        try:
            sess = self.brain.create_session(metadata={"harness": "runtime"})
            if isinstance(sess, dict):
                return (
                    sess.get("session_id")
                    or sess.get("id")
                    or sess.get("sessionId")
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("auto session create failed: %s", exc)
        return None

    def _workspace_for(self, session_id: Optional[str]) -> Optional[str]:
        try:
            from nak.plugins.tool.workspace import ensure_session_workspace

            root = self._workspace_root
            project = ensure_session_workspace(session_id or "default", root=root)
            return str(project)
        except Exception as exc:  # noqa: BLE001
            logger.debug("workspace create failed: %s", exc)
            return None

    # -- state -------------------------------------------------------------

    def create_state(self, request: str, session_id: Optional[str] = None) -> HarnessState:
        sid = session_id or self.session_id
        ws = self._workspace_for(sid)
        state = create_state(request, session_id=sid, workspace=ws)
        state.next_turn()
        # R4: restore the session's carried plan (if any) into the new turn.
        key = sid or "default"
        carried = self._session_plans.get(key)
        if carried:
            state.plan = list(carried)
        return state

    # -- continuity (R3) -------------------------------------------------------

    def _notes_key(self, state: HarnessState) -> str:
        return state.session_id or "default"

    def _workspace_file_list(self, workspace: Optional[str], limit: int = 20) -> List[str]:
        """Relative paths present in the task workspace (best-effort)."""
        if not workspace:
            return []
        try:
            from nak.plugins.tool.file_tool import list_files

            data = list_files(".", root=workspace, max_results=limit)
            return [e for e in data.get("entries", [])
                    if "__pycache__" not in e][:limit]
        except Exception:  # noqa: BLE001
            return []

    def _workspace_context(self, state: HarnessState) -> str:
        """Follow-up context for turn N>0: plan + files present + prior summaries.

        Returns "" on a first turn (nothing to build on) so single-turn
        prompts stay byte-identical to before. R15 appends a bounded Mind
        recall block when project memory has hits for this session.
        """
        notes = self._session_notes.get(self._notes_key(state), [])
        files = self._workspace_file_list(state.workspace)
        plan = list(state.plan or [])
        recalled = ""
        try:
            from .project_memory import recall_for_turn

            recalled = recall_for_turn(self.brain, state, state.request) or ""
        except Exception:  # noqa: BLE001
            recalled = ""
        if not notes and not files and not plan and not recalled:
            return ""
        parts = ["[Workspace continuity — build on the work below, don't redo it]"]
        if plan:
            parts.append("Current plan:\n"
                         + "\n".join(f"- {p}" for p in plan))
        if files:
            parts.append("Files already in the workspace:\n"
                         + "\n".join(f"- {f}" for f in files))
        if notes:
            parts.append("Previous turns in this workspace:\n"
                         + "\n".join(f"- {n}" for n in notes[-3:]))
        if recalled:
            parts.append(recalled)
        return "\n".join(parts)

    def _remember_turn(self, state: HarnessState, agent_name: str, answer: str) -> None:
        """File one turn's outcome + plan for follow-ups (capped, best-effort).

        R15: also persists a namespaced one-liner to Mind so a restarted
        runtime can recall it. Mind failures never fail the turn.
        """
        try:
            key = self._notes_key(state)
            notes = self._session_notes.setdefault(key, [])
            notes.append(f"{agent_name} ({state.status}): {(answer or '')[:200]}")
            del notes[:-3]
            if state.plan:
                self._session_plans[key] = list(state.plan)
            else:
                # Explicit clear (or never planned): don't resurrect stale steps.
                # Untouched turns keep their restored copy, so this only drops
                # plans the turn itself emptied.
                self._session_plans.pop(key, None)
            while len(self._session_notes) > 10:
                dropped = next(iter(self._session_notes))
                self._session_notes.pop(dropped)
                self._session_plans.pop(dropped, None)
        except Exception:  # noqa: BLE001
            logger.debug("remember turn failed")
        try:
            from .project_memory import store_turn

            store_turn(self.brain, state, agent_name, answer)
        except Exception:  # noqa: BLE001
            logger.debug("project memory store failed")

    # -- decide --------------------------------------------------------------

    async def decide(self, state: HarnessState) -> DecisionResult:
        """Full-list LLM routing: Polaris always sees every registered
        agent (name + description + capabilities) and takes its own decision.
        No code pre-filtering by default — the LLM judges with full context.
        Stateless (decision prompt never pollutes agent history).
        (The instant-route shortcut bypasses this method entirely — it lives
        in Polaris.decide_and_run(), which matches AND executes.)"""
        try:
            result: DecisionResult = await self.decision.decide(
                state.request, session_id=None
            )
        except BrainError as exc:
            return DecisionResult(
                action="ASK_USER",
                agent_name=None,
                reason=f"NebulonMind unavailable ({exc}, status={exc.status}). "
                "Please retry or clarify your request.",
                confidence=0.0,
                parameters={},
            )
        state.decision = result.to_dict()
        state.agent = result.agent_name
        state.record_observation(
            "decision",
            f"{result.action} agent={result.agent_name} conf={result.confidence:.2f}",
        )
        return result

    # -- main turn -------------------------------------------------------------

    async def handle(
        self,
        text: str,
        *,
        auto_create: bool = False,
        state: Optional[HarnessState] = None,
        instant_route: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """One harness turn. ALWAYS decides first; owns state + workspace.
        instant_route=None follows the runtime/Polaris mode; True/False
        overrides it for this turn only (user consent passed from pipeline).
        When instant-route fires, the match+decide+execute all happened inside
        Polaris.decide_and_run() — this method only observes/verifies."""
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        text = text.strip()
        st = state or self.create_state(text)
        st.request = text
        st.history.append({"role": "user", "content": text})
        # Phase 6: expose this turn's state to stateful tools (create_plan /
        # update_plan). Cleared in _result(), which every return path uses.
        from nak.plugins.tool.workspace import set_current_harness_state

        set_current_harness_state(st)

        fp = self.instant_route if instant_route is None else bool(instant_route)
        agent_task: Optional[str] = None
        turn_session: Optional[str] = None
        if fp:
            # Action lives in Polaris: explicit name -> executed there.
            context = self._workspace_context(st)
            agent_task = f"{context}\n\nCurrent request: {text}" if context else text
            turn_session = self._open_session() or st.session_id or self.session_id
            run = await self.decision.decide_and_run(
                text, task=agent_task, session_id=turn_session, state=st,
                instant_route=True,
            )
            decision = run["decision"]
            st.decision = decision.to_dict()
            st.agent = decision.agent_name
            st.record_observation(
                "decision",
                f"{decision.action} agent={decision.agent_name} conf={decision.confidence:.2f}",
            )
            self._history.append({"role": "user", "content": text})
            if run.get("executed"):
                exec_res = {
                    "answer": str(run.get("answer") or ""),
                    "run_on": str(run.get("run_on") or decision.agent_name or ""),
                    "steps": run.get("steps") or [],
                }
                st.history.append({"role": "assistant", "content": exec_res["answer"]})
                self._history.append({"role": "assistant", "content": exec_res["answer"]})
                return await self._finish_exec(st, decision, exec_res, text, turn_session)
            # No instant hit (or named agent missing): fall through to the
            # normal ASK/CREATE/USE flow with the LLM decision already in hand
            # (agent_task/turn_session computed above are reused below).
        else:
            decision = await self.decide(st)
            self._history.append({"role": "user", "content": text})

        # ASK_USER -> question only
        if decision.is_ask:
            question = decision.reason or "Could you clarify your request?"
            st.history.append({"role": "assistant", "content": question})
            st.finish(STATUS_WAITING_USER)
            self._history.append({"role": "assistant", "content": question})
            return self._result(st, decision, question, "Polaris", False, None, [])

        # CREATE_AGENT -> proposal (or auto-create when explicitly asked)
        if decision.is_create:
            if auto_create:
                async def _yes(_spec: DecisionResult) -> bool:
                    return True

                try:
                    created = await self.creator.create(decision, _yes)
                except (FileExistsError, ValueError) as exc:
                    st.finish(STATUS_WAITING_USER)
                    return self._result(
                        st, decision,
                        f"Agent {decision.agent_name} already exists: {exc}. "
                        "Please retry your request so it routes via USE_AGENT.",
                        "Polaris", False, None, [],
                    )
                created_str = str(created) if created else None
                st.finish(STATUS_DONE)
                return self._result(
                    st, decision,
                    f"Created agent {decision.agent_name} at {created_str}. "
                    "Please retry your request so it routes via USE_AGENT.",
                    f"Genesis:{decision.agent_name}", False, created_str, [],
                )
            st.finish(STATUS_NEEDS_CONFIRM)
            proposal = (
                f"No suitable agent found for: {text[:200]}\n"
                f"Proposed: create '{decision.agent_name}' — {decision.reason}"
            )
            return self._result(st, decision, proposal, "Polaris", True, None, [])

        # USE_AGENT -> run ONLY if resolvable, else create proposal
        agent_name = decision.agent_name or ""
        if not agent_name:
            st.finish(STATUS_WAITING_USER)
            return self._result(
                st, decision,
                "Decision returned USE_AGENT without an agent_name. "
                "Please clarify which agent should handle this.",
                "Polaris", False, None, [],
            )
        if not self.agents.is_resolvable(agent_name):
            alt = dict(decision.to_dict())
            alt["action"] = "CREATE_AGENT"
            alt["agent_name"] = agent_name
            st.finish(STATUS_NEEDS_CONFIRM)
            proposal = (
                f"Decision selected '{agent_name}' but it is not installed "
                f"(missing nak/agents/{agent_name}/).\n"
                f"Reason: {decision.reason}"
            )
            return self._result(st, DecisionResult.from_dict(alt), proposal,
                                "Polaris", True, None, [])
        # R3: follow-up turns inherit workspace context (files + summaries);
        # first turns are unaffected (context is "").
        # Per-turn Mind session: server-side chat history grows unboundedly
        # within one session (each agent step resends history+transcript),
        # which eventually exceeds the provider's max prompt length (400s
        # observed live on turn 2+). A fresh session per turn keeps prompts
        # bounded; cross-turn memory travels explicitly via workspace context.
        # Falls back to the shared session when Mind can't mint one.
        # (Instant-fallthrough turns reuse the task/session minted above.)
        if agent_task is None or turn_session is None:
            context = self._workspace_context(st)
            agent_task = f"{context}\n\nCurrent request: {text}" if context else text
            turn_session = self._open_session() or st.session_id or self.session_id
        try:
            exec_res = await self.agents.run_text(
                agent_name, agent_task, session_id=turn_session, state=st
            )
        except BrainError as exc:
            st.fail(f"Agent {agent_name} failed: Mind unavailable ({exc}).")
            self._remember_turn(st, agent_name, f"Mind unavailable: {exc}")
            return self._result(
                st, decision,
                f"Agent {agent_name} failed: Mind unavailable ({exc}).",
                agent_name, False, None, [],
            )
        st.history.append({"role": "assistant", "content": exec_res["answer"]})
        self._history.append({"role": "assistant", "content": exec_res["answer"]})
        return await self._finish_exec(st, decision, exec_res, text, turn_session)

    async def _finish_exec(
        self,
        st: HarnessState,
        decision: DecisionResult,
        exec_res: Dict[str, Any],
        text: str,
        turn_session: Optional[str],
    ) -> Dict[str, Any]:
        """Shared tail for executed turns (instant-run inside Polaris or
        USE_AGENT run here): observe -> verify -> bounded recover -> done."""
        self.observe(st, exec_res["answer"])
        # Phase 4+5: verify, then bounded auto-recovery (fix -> re-verify).
        answer = exec_res["answer"]
        run_on = exec_res["run_on"]
        steps = exec_res.get("steps", [])
        if self.should_verify(st):
            verification = self.verify(st)
            while not verification["ok"] and self.should_recover(st):
                fixed = await self.recover(st, text, session_id=turn_session)
                answer = str(fixed.get("answer") or answer)
                run_on = str(fixed.get("run_on") or run_on)
                steps = fixed.get("steps", steps)
                st.history.append({"role": "assistant", "content": answer})
                self._history.append({"role": "assistant", "content": answer})
                self.observe(st, answer)
                verification = self.verify(st)
            if not verification["ok"]:
                st.fail(f"verification failed: {verification['failures']}")
                note = (f"\n[verification FAILED: {', '.join(verification['failures'])}"
                        f" — see {verification.get('log', 'workspace/logs/')}]")
                if st.recovery_attempts:
                    note += f" ({st.recovery_attempts} recovery attempt(s) tried)"
                self._remember_turn(st, run_on, answer)
                return self._result(
                    st, decision, answer + note, run_on,
                    False, None, steps,
                )
        st.finish(STATUS_DONE)
        self._remember_turn(st, run_on, answer)
        result = self._result(
            st, decision, answer, run_on,
            False, None, steps,
        )
        result["recovered"] = st.recovery_attempts > 0
        return result

    # -- lifecycle hooks (Phase 3 observer / Phase 4 verifier / Phase 5 recovery) --

    def observe(self, state: HarnessState, result: str) -> None:
        """Classify + record a final-answer observation (Phase 3)."""
        from .events import log_event
        from .observer import inspect as classify

        verdict = classify({"answer": result or ""})
        log_event(state, "answer",
                  f"{verdict['status']}: {(result or '')[:400]}",
                  classification=verdict["status"])

    def should_verify(self, state: HarnessState) -> bool:
        """Phase 4: verify when the turn did real work in an isolated workspace.

        Skips pure-answer turns (no tool calls) and turns without a workspace
        so ASK_USER / question-only flows stay untouched.
        """
        if not state.tool_calls:
            return False
        return bool(state.workspace)

    def verify(self, state: HarnessState) -> Dict[str, Any]:
        """Run independent checks; record summary on state. Never raises."""
        from .events import log_event
        from .verifier import Verifier

        try:
            result = Verifier(state.workspace).verify()  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "failures": ["verifier_error"],
                      "checks": [], "logs": str(exc)[:500]}
        try:
            state.verification_results.append({
                "ok": result["ok"],
                "failures": list(result.get("failures", [])),
                "log": str(result.get("log", "")),
            })
        except Exception:  # noqa: BLE001
            pass
        log_event(state, "verify",
                  f"verification {'OK' if result['ok'] else 'FAILED: ' + str(result.get('failures'))[:200]}",
                  classification="SUCCESS" if result["ok"] else "FAILURE")
        return result

    def should_recover(self, state: HarnessState) -> bool:
        """Phase 5: True when the last verification failed AND budget remains
        AND the failure does not need a human (no recent policy denials)."""
        from .recovery import decide_strategy, ESCALATE

        results = state.verification_results or []
        if not results or results[-1].get("ok"):
            return False
        return decide_strategy(state)["strategy"] != ESCALATE

    async def recover(self, state: HarnessState, original_request: str,
                      session_id: Optional[str] = None) -> Dict[str, Any]:
        """One bounded fix attempt via nak.harness.recovery. Never raises."""
        from .recovery import recover as _recover

        return await _recover(state, self, original_request, session_id=session_id)

    # -- compat helpers ----------------------------------------------------------

    def _result(
        self,
        state: HarnessState,
        decision: DecisionResult,
        answer: str,
        run_on: str,
        needs_confirm: bool,
        created_path: Optional[str],
        steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        from nak.plugins.tool.workspace import set_current_harness_state

        set_current_harness_state(None)  # end of turn: plan tools go inert
        if not steps and state.tool_calls:
            steps = [
                {
                    "tool": t.get("tool", "?"),
                    "ok": t.get("ok", True),
                    **({"denied": True} if t.get("denied") else {}),
                }
                for t in state.tool_calls
            ]
        self._persist_state(state)
        return {
            "decision": decision.to_dict(),
            "answer": answer,
            "run_on": run_on,
            "routed_via": run_on,
            "needs_confirm": needs_confirm,
            "created_path": created_path,
            "session_id": state.session_id or self.session_id,
            "steps": steps,
            "state": state.to_dict(),
            "verification": list(state.verification_results),
            "workspace": state.workspace,
        }

    def _persist_state(self, state: HarnessState) -> None:
        """Best-effort workspace/state.json snapshot (never fails the turn)."""
        try:
            ws = state.workspace
            if not ws:
                return
            project = Path(ws)
            state_file = project.parent / "state.json"
            state_file.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, default=str, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("state persist failed: %s", exc)

    async def create_confirmed(self, decision_dict: Dict[str, Any]) -> Optional[Path]:
        spec = DecisionResult.from_dict(decision_dict)
        if spec.action != "CREATE_AGENT":
            spec = DecisionResult(
                action="CREATE_AGENT",
                agent_name=spec.agent_name,
                reason=spec.reason,
                confidence=spec.confidence,
                parameters=spec.parameters,
            )

        async def _yes(_s: DecisionResult) -> bool:
            return True

        return await self.creator.create(spec, _yes)

    def is_agent_resolvable(self, agent_name: str) -> bool:
        return self.agents.is_resolvable(agent_name)

    async def run_agent_text(self, agent_name: str, text: str, **kwargs: Any) -> Dict[str, Any]:
        return await self.agents.run_text(
            agent_name, text,
            session_id=kwargs.get("session_id", self.session_id),
            **{k: v for k, v in kwargs.items() if k != "session_id"},
        )

    def list_agents_str(self) -> str:
        try:
            return self.registry.available_agents_str()
        except Exception as exc:  # noqa: BLE001
            return f"(registry error: {exc})"

    def reset_session(self) -> Optional[str]:
        self.session_id = self._open_session()
        self._history.clear()
        return self.session_id

    # -- sync convenience ----------------------------------------------------------

    def handle_sync(self, text: str, **kwargs: Any) -> Dict[str, Any]:
        return asyncio.run(self.handle(text, **kwargs))


__all__ = ["HarnessRuntime"]
