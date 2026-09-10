"""
nak.agents.DecisionAgent.agent -- async router that uses NebulonMind only.

All decisions go via Brain.chat() (POST /agent/chat) which does recall+remember+decide
in-process on NebulonMind. No direct openai client here (per approved plan #3).

Pattern (Phase 1): await asyncio.to_thread(brain.chat, prompt)
- Reuses sync Brain (requests.Session) without new deps
- Respects Brain.chat timeout = max(timeout, write_timeout, 60)
- Protected by asyncio.Lock when sharing one Brain across tasks

Future Phase 2: promote httpx to runtime and add nak/brain/async_client.py -> AsyncBrain
for true async I/O (see utils/agent_registry thread-safety notes).

Prompt is loaded from nak/prompts/decision.md with {available_agents}{user_request}.
LLM must return ONLY JSON -> parsed into DecisionResult.
On invalid JSON / BrainError -> fallback ASK_USER (never raises to caller unless strict=True).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Any, Dict

from nak.brain.client import Brain, BrainError
from nak.utils.agent_registry import AgentRegistry

from .models import DecisionResult, VALID_ACTIONS

logger = logging.getLogger("nak.agents.DecisionAgent")

try:
    from nak.prompts import render_prompt, load_prompt
except Exception:  # fallback if prompts package missing
    render_prompt = None  # type: ignore
    load_prompt = None  # type: ignore


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class DecisionAgent:
    """Decides which agent should handle a user request. Mind-only, async."""

    name = "DecisionAgent"

    def __init__(
        self,
        brain: Optional[Brain] = None,
        registry: Optional[AgentRegistry] = None,
        prompt_name: str = "decision",
    ) -> None:
        self.brain = brain or Brain()
        self.registry = registry or AgentRegistry()
        self.prompt_name = prompt_name
        self._lock = asyncio.Lock()

    # -- prompt ----------------------------------------------------------------

    def _build_prompt(self, user_request: str, available_agents: Optional[List[str]] = None) -> str:
        if available_agents is not None:
            agents_str = "; ".join(available_agents) if available_agents else "(no agents registered yet)"
        else:
            # registry JSON leader
            try:
                agents_str = self.registry.available_agents_str()
            except Exception:
                agents_str = "(no agents registered yet)"
        # hybrid memory fallback: if agents_str is empty, try brain.search (best-effort, no raise)
        # not done here — caller can enrich if desired; keep prompt build pure.

        # Prompt folder is the single source of truth — no inline copies.
        # A missing file fails loud (FileNotFoundError) instead of silently
        # deciding from a stale duplicated prompt.
        if render_prompt is None or load_prompt is None:
            raise FileNotFoundError(
                f"prompt {self.prompt_name!r} unavailable (nak.prompts package missing)"
            )
        try:
            return render_prompt(self.prompt_name, available_agents=agents_str, user_request=user_request)
        except FileNotFoundError:
            raise
        except Exception as exc:
            logger.debug("render_prompt failed, using raw template: %s", exc)
        tmpl = load_prompt(self.prompt_name)
        # safe replace (preserve JSON braces)
        return tmpl.replace("{available_agents}", agents_str).replace("{user_request}", user_request)

    # -- parse -----------------------------------------------------------------

    def _parse_response(self, response: Any) -> DecisionResult:
        """Parse LLM answer string into DecisionResult. Tries strict JSON, then extract JSON substring."""
        text = response if isinstance(response, str) else str(response)
        text = text.strip()
        if not text:
            raise ValueError("empty LLM response")

        # Try direct JSON
        data: Optional[Dict[str, Any]] = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract first JSON object (LLM may wrap in markdown)
            m = _JSON_RE.search(text)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    data = None
        if data is None or not isinstance(data, dict):
            raise ValueError(f"LLM response is not JSON: {text[:500]!r}")

        # Normalize keys (lowercase)
        # accept variations: agent, agent_name, name
        action = str(data.get("action") or data.get("Action") or "").strip().upper()
        if not action:
            raise ValueError(f"missing 'action' in {data}")
        if action not in VALID_ACTIONS:
            # try to map common synonyms
            mapping = {"USE": "USE_AGENT", "CREATE": "CREATE_AGENT", "ASK": "ASK_USER", "CREATE_NEW": "CREATE_AGENT"}
            action = mapping.get(action, action)
        if action not in VALID_ACTIONS:
            raise ValueError(f"invalid action {action!r}")

        agent_name = data.get("agent_name")
        if agent_name is None:
            agent_name = data.get("agent") if "agent" in data else data.get("name")
        reason = str(data.get("reason") or data.get("explanation") or "")
        confidence = data.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.0
        parameters = data.get("parameters") or data.get("params") or {}
        if not isinstance(parameters, dict):
            parameters = {}

        return DecisionResult(
            action=action,  # type: ignore
            agent_name=agent_name,
            reason=reason,
            confidence=confidence,
            parameters=parameters,
        )

    # -- decide ----------------------------------------------------------------

    async def decide(
        self,
        user_request: str,
        available_agents: Optional[List[str]] = None,
        strict: bool = False,
        session_id: Optional[str] = None,
    ) -> DecisionResult:
        """
        Analyze request and decide what should happen. Always via NebulonMind.

        Args:
            user_request: natural language request (required)
            available_agents: override list for prompt; if None uses registry.available_agents_str()
            strict: if True, raises on parse/BrainError; if False (default) returns ASK_USER fallback
            session_id: optional persistent session for brain.chat
        """
        if not user_request or not user_request.strip():
            raise ValueError("user_request must be non-empty")
        user_request = user_request.strip()

        prompt = self._build_prompt(user_request, available_agents)

        # Mind-only: Brain.chat -> POST /agent/chat (sync) offloaded to thread
        # Use lock to avoid requests.Session thread-safety race when sharing one Brain
        try:
            async with self._lock:
                raw = await asyncio.to_thread(self.brain.chat, prompt, session_id=session_id)
        except BrainError as exc:
            logger.warning("DecisionAgent brain.chat BrainError: %s (status=%s)", exc, exc.status)
            if strict:
                raise
            return DecisionResult(
                action="ASK_USER",
                agent_name=None,
                reason=f"NebulonMind unavailable: {exc} (status={exc.status}) — please clarify or retry",
                confidence=0.0,
                parameters={},
            )
        except Exception as exc:
            logger.warning("DecisionAgent brain.chat failed: %s", exc)
            if strict:
                raise
            return DecisionResult(
                action="ASK_USER",
                agent_name=None,
                reason=f"Decision failed: {exc} — please rephrase",
                confidence=0.0,
                parameters={},
            )

        # raw is dict {answer, turn, ...} from Brain.chat:406
        answer = ""
        if isinstance(raw, dict):
            answer = str(raw.get("answer") or raw.get("content") or raw.get("text") or "")
            if not answer:
                # fallback: dump whole dict if answer missing
                try:
                    answer = json.dumps(raw, ensure_ascii=False)
                except Exception:
                    answer = str(raw)
        else:
            answer = str(raw)

        try:
            return self._parse_response(answer)
        except Exception as exc:
            logger.warning("DecisionAgent parse failed: %s; raw answer: %s", exc, answer[:600])
            if strict:
                raise
            return DecisionResult(
                action="ASK_USER",
                agent_name=None,
                reason=f"Could not parse decision: {exc}. Please clarify your request.",
                confidence=0.0,
                parameters={"raw_answer": answer[:1000]},
            )

    # -- optional semantic enrichment ------------------------------------------

    async def decide_with_memory_fallback(
        self,
        user_request: str,
        top_k: int = 5,
        confidence_threshold: float = 0.7,
        **kwargs,
    ) -> DecisionResult:
        """Calls decide(), and if confidence < threshold, enriches with brain.search hits (Memory index)."""
        result = await self.decide(user_request, **kwargs)
        if result.confidence >= confidence_threshold or result.action == "ASK_USER":
            return result
        # try to enrich available_agents via semantic search (best-effort)
        try:
            hits = await asyncio.to_thread(self.brain.search, user_request, top_k=top_k)  # type: ignore
            if hits:
                # if hits mention an agent not in result, keep but attach to parameters for downstream
                result.parameters["memory_hits"] = hits[:3]
        except Exception as exc:
            logger.debug("memory fallback search failed: %s", exc)
        return result
