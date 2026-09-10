"""
pipeline.chatagent.pipeline -- production DecisionAgent router + execution.

Contract (production-ready):
    1. EVERY user turn goes through DecisionAgent.decide() (stateless).
       No direct Brain.chat fallback, no silent Example/SimpleAgent use.
    2. Decision determines execution:
       USE_AGENT    -> run that agent ONLY if it resolves on disk.
                       If missing -> ask user to create it (needs_confirm=True).
       CREATE_AGENT -> NEVER auto-create. Return proposal, terminal asks y/N.
       ASK_USER     -> return decision.reason as the clarifying question.
                       No task execution.
    3. Every result carries `run_on` so the terminal can show:
       "Running on: <agent>" / "Running on: DecisionAgent (question/proposal)".

Agent load convention:
    nak.agents.<Slug>.agent.Agent   (AgentCreator template: run/run_sync)
    nak.agents.Example.base.SimpleAgent (legacy Example only)

If an agent does not resolve -> FileNotFoundError path, converted by
handle() into a create-proposal (needs_confirm=True), never a fallback chat.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from nak.agents.AgentCreator.agent import AgentCreator
from nak.agents.DecisionAgent.agent import DecisionAgent
from nak.agents.DecisionAgent.models import DecisionResult
from nak.brain.client import Brain, BrainError
from nak.utils.agent_registry import AgentRegistry

logger = logging.getLogger("pipeline.chatagent")


class ChatAgentPipeline:
    """Production pipeline: decide -> route -> run-or-ask."""

    def __init__(
        self,
        brain: Optional[Brain] = None,
        registry: Optional[AgentRegistry] = None,
        decision_agent: Optional[DecisionAgent] = None,
        creator: Optional[AgentCreator] = None,
        session_id: Optional[str] = None,
        auto_session: bool = True,
    ) -> None:
        self.brain = brain or Brain()
        self.registry = registry or AgentRegistry()
        self.decision = decision_agent or DecisionAgent(
            brain=self.brain, registry=self.registry
        )
        self.creator = creator or AgentCreator(
            brain=self.brain, registry=self.registry
        )
        self.session_id = session_id
        if auto_session and self.session_id is None:
            try:
                sess = self.brain.create_session(
                    metadata={"pipeline": "chatagent"}
                )
                if isinstance(sess, dict):
                    self.session_id = (
                        sess.get("session_id")
                        or sess.get("id")
                        or sess.get("sessionId")
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("auto session create failed: %s", exc)
                self.session_id = None
        self._history: List[Dict[str, str]] = []

    # -- strict agent resolution -----------------------------------------

    def is_agent_resolvable(self, agent_name: str) -> bool:
        """True if the agent can actually be loaded (disk + importable)."""
        if not agent_name:
            return False
        # 1. Generated convention: nak/agents/<Slug>/agent.py -> Agent
        try:
            mod = importlib.import_module(f"nak.agents.{agent_name}.agent")
            if getattr(mod, "Agent", None) is not None:
                return True
        except ModuleNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            return False
        # 2. Legacy Example: nak/agents/Example/base.py -> SimpleAgent
        if agent_name.lower() == "example":
            try:
                mod = importlib.import_module("nak.agents.Example.base")
                return getattr(mod, "SimpleAgent", None) is not None
            except Exception:  # noqa: BLE001
                return False
        return False

    async def run_agent_text(
        self, agent_name: str, text: str, **kwargs: Any
    ) -> Dict[str, Any]:
        """Run a resolved agent. STRICT: raises if not resolvable.

        Raises:
            FileNotFoundError: agent not on disk / not importable.
            BrainError: Mind unreachable during agent run.
        """
        if not self.is_agent_resolvable(agent_name):
            raise FileNotFoundError(
                f"agent {agent_name!r} is not installed "
                f"(missing nak/agents/{agent_name}/agent.py)"
            )
        # Generated agents
        try:
            mod = importlib.import_module(f"nak.agents.{agent_name}.agent")
        except ModuleNotFoundError:
            mod = None  # type: ignore
        if mod is not None and getattr(mod, "Agent", None) is not None:
            cls = getattr(mod, "Agent")
            inst = cls(brain=self.brain)
            if hasattr(inst, "run") and asyncio.iscoroutinefunction(
                getattr(inst, "run")
            ):
                data = await inst.run(text, session_id=self.session_id, **kwargs)
            elif hasattr(inst, "run_sync"):
                data = await asyncio.to_thread(
                    inst.run_sync, text, session_id=self.session_id, **kwargs
                )
            elif hasattr(inst, "run"):
                data = await asyncio.to_thread(
                    inst.run, text, session_id=self.session_id, **kwargs
                )
            else:
                raise AttributeError(f"Agent {agent_name} has no run/run_sync")
            answer = str(
                data.get("answer") or data.get("content") or data.get("text") or ""
            )
            steps = data.get("steps") if isinstance(data, dict) else None
            return {"answer": answer, "run_on": agent_name, "raw": data,
                    "steps": list(steps) if isinstance(steps, list) else []}

        # Legacy Example
        from nak.agents.Example.base import SimpleAgent  # type: ignore

        agent = SimpleAgent(brain=self.brain)
        data = await asyncio.to_thread(agent.run, text, session_id=self.session_id)
        return {
            "answer": str(data.get("answer") or ""),
            "run_on": "Example",
            "raw": data,
            "steps": [],
        }

    # -- main turn: decide -> route --------------------------------------

    async def handle(
        self,
        text: str,
        *,
        auto_create: bool = False,
    ) -> Dict[str, Any]:
        """One pipeline turn. ALWAYS decides first.

        Returns:
            {
              "decision": DecisionResult.to_dict(),
              "answer": str,            # agent answer OR question OR proposal
              "run_on": str,            # <-- show this: "Running on: X"
              "routed_via": str,        # compat alias of run_on
              "needs_confirm": bool,    # True -> terminal must ask to create
              "created_path": str|None,
              "session_id": str|None,
            }
        """
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        text = text.strip()

        # 1. DECIDE (stateless so the decision prompt never pollutes chat history)
        try:
            decision: DecisionResult = await self.decision.decide(
                text, session_id=None
            )
        except BrainError as exc:
            # Mind down -> honest fallback question, NO silent execution
            fb = DecisionResult(
                action="ASK_USER",
                agent_name=None,
                reason=f"NebulonMind unavailable ({exc}, status={exc.status}). "
                "Please retry or clarify your request.",
                confidence=0.0,
                parameters={},
            )
            return {
                "decision": fb.to_dict(),
                "answer": fb.reason,
                "run_on": "DecisionAgent (fallback)",
                "routed_via": "DecisionAgent (fallback)",
                "needs_confirm": False,
                "created_path": None,
                "session_id": self.session_id,
            }

        self._history.append({"role": "user", "content": text})

        # 2a. ASK_USER -> ask, do NOT execute any task agent
        if decision.is_ask:
            question = decision.reason or "Could you clarify your request?"
            self._history.append({"role": "assistant", "content": question})
            return {
                "decision": decision.to_dict(),
                "answer": question,
                "run_on": "DecisionAgent",
                "routed_via": "DecisionAgent",
                "needs_confirm": False,
                "created_path": None,
                "session_id": self.session_id,
            }

        # 2b. CREATE_AGENT -> propose the model's name as-is
        # (generic naming is enforced by the decision/agent_naming prompts),
        # NEVER auto-run. Terminal asks y/N.
        if decision.is_create:
            if auto_create:
                async def _yes(_spec: DecisionResult) -> bool:
                    return True

                try:
                    created = await self.creator.create(decision, _yes)
                except (FileExistsError, ValueError) as exc:
                    return {
                        "decision": decision.to_dict(),
                        "answer": f"Agent {decision.agent_name} already exists: {exc}. "
                        "Please retry your request so it routes via USE_AGENT.",
                        "run_on": "DecisionAgent",
                        "routed_via": "DecisionAgent",
                        "needs_confirm": False,
                        "created_path": None,
                        "session_id": self.session_id,
                    }
                created_str = str(created) if created else None
                return {
                    "decision": decision.to_dict(),
                    "answer": f"Created agent {decision.agent_name} at {created_str}. "
                    "Please retry your request so it routes via USE_AGENT.",
                    "run_on": f"AgentCreator:{decision.agent_name}",
                    "routed_via": f"AgentCreator:{decision.agent_name}",
                    "needs_confirm": False,
                    "created_path": created_str,
                    "session_id": self.session_id,
                }
            proposal = (
                f"No suitable agent found for: {text[:200]}\n"
                f"Proposed: create '{decision.agent_name}' — {decision.reason}"
            )
            return {
                "decision": decision.to_dict(),
                "answer": proposal,
                "run_on": "DecisionAgent",
                "routed_via": "DecisionAgent",
                "needs_confirm": True,
                "created_path": None,
                "session_id": self.session_id,
            }

        # 2c. USE_AGENT -> run ONLY if resolvable, else ask to create
        agent_name = decision.agent_name or ""
        if not agent_name:
            return {
                "decision": decision.to_dict(),
                "answer": "Decision returned USE_AGENT without an agent_name. "
                "Please clarify which agent should handle this.",
                "run_on": "DecisionAgent",
                "routed_via": "DecisionAgent",
                "needs_confirm": False,
                "created_path": None,
                "session_id": self.session_id,
            }
        if not self.is_agent_resolvable(agent_name):
            proposal = (
                f"Decision selected '{agent_name}' but it is not installed "
                f"(missing nak/agents/{agent_name}/).\n"
                f"Reason: {decision.reason}"
            )
            alt = dict(decision.to_dict())
            # re-target the confirm flow at the missing agent
            alt["action"] = "CREATE_AGENT"
            alt["agent_name"] = agent_name
            return {
                "decision": alt,
                "answer": proposal,
                "run_on": "DecisionAgent",
                "routed_via": "DecisionAgent",
                "needs_confirm": True,
                "created_path": None,
                "session_id": self.session_id,
            }
        try:
            exec_res = await self.run_agent_text(agent_name, text)
        except BrainError as exc:
            return {
                "decision": decision.to_dict(),
                "answer": f"Agent {agent_name} failed: Mind unavailable ({exc}).",
                "run_on": agent_name,
                "routed_via": agent_name,
                "needs_confirm": False,
                "created_path": None,
                "session_id": self.session_id,
                "steps": [],
            }
        self._history.append(
            {"role": "assistant", "content": exec_res["answer"]}
        )
        return {
            "decision": decision.to_dict(),
            "answer": exec_res["answer"],
            "run_on": exec_res["run_on"],
            "routed_via": exec_res["run_on"],
            "needs_confirm": False,
            "created_path": None,
            "session_id": self.session_id,
            "steps": exec_res.get("steps", []),
        }

    # -- confirm-gated creation for the terminal loop --------------------

    async def create_confirmed(self, decision_dict: Dict[str, Any]) -> Optional[Path]:
        """Create the proposed agent after terminal y/N (confirm-gated)."""
        spec = DecisionResult.from_dict(decision_dict)
        if spec.action != "CREATE_AGENT":
            # USE_AGENT-missing path re-targets here; normalize
            spec = DecisionResult(
                action="CREATE_AGENT",
                agent_name=spec.agent_name,
                reason=spec.reason,
                confidence=spec.confidence,
                parameters=spec.parameters,
            )

        async def _yes(_s: DecisionResult) -> bool:
            return True  # terminal already asked; gate still enforced via callback

        return await self.creator.create(spec, _yes)

    # -- helpers ----------------------------------------------------------

    def list_agents_str(self) -> str:
        try:
            return self.registry.available_agents_str()
        except Exception as exc:  # noqa: BLE001
            return f"(registry error: {exc})"

    def reset_session(self) -> Optional[str]:
        try:
            sess = self.brain.create_session(metadata={"pipeline": "chatagent"})
            if isinstance(sess, dict):
                self.session_id = (
                    sess.get("session_id") or sess.get("id") or sess.get("sessionId")
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("reset session failed: %s", exc)
            self.session_id = None
        self._history.clear()
        return self.session_id
