"""
nak.agents.Polaris.agent -- async router that uses NebulonMind only.

All decisions go via Brain.chat() (POST /agent/chat) which does recall+remember+decide
in-process on NebulonMind. No direct openai client here (per approved plan #3).

Pattern (Phase 1): await asyncio.to_thread(brain.chat, prompt)
- Reuses sync Brain (requests.Session) without new deps
- Respects Brain.chat timeout = max(timeout, write_timeout, 60)
- Protected by asyncio.Lock when sharing one Brain across tasks

Future Phase 2: promote httpx to runtime and add nak/brain/async_client.py -> AsyncBrain
for true async I/O (see utils/agent_registry thread-safety notes).

Prompt is loaded from nak/prompts/polaris.md with {available_agents}{user_request}.
LLM must return ONLY JSON -> parsed into DecisionResult.
On invalid JSON / BrainError -> fallback ASK_USER (never raises to caller unless strict=True).

Token-saving instant_route (user-consent mode, default OFF):
- The mode lives HERE on Polaris (instant_route ctor arg or per-call
  decide_and_run(..., instant_route=...)). No cfg knob — the pipeline only
  passes the user's consent down; Polaris never reads config for this.
- When enabled and the user message EXPLICITLY names exactly one registered
  agent ("ask Apollo ...", "use testing agent"), Polaris itself
  matches the name, decides USE_AGENT, AND runs that agent — 0 LLM calls,
  auto-moving to the respected agent. The pipeline does not route this path.
- Ambiguous (0 or 2+ names) or non-matching messages fall through to the
  normal Mind path. No fuzzy keyword triggers — name mention only, so
  paraphrase/negation risk from the reverted R16 approach is avoided.
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

logger = logging.getLogger("nak.agents.Polaris")

try:
    from nak.prompts import render_prompt, load_prompt
except Exception:  # fallback if prompts package missing
    render_prompt = None  # type: ignore
    load_prompt = None  # type: ignore


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class Polaris:
    """Decides which agent should handle a user request. Mind-only, async."""

    name = "Polaris"

    def __init__(
        self,
        brain: Optional[Brain] = None,
        registry: Optional[AgentRegistry] = None,
        prompt_name: str = "polaris",
        instant_route: bool = False,
    ) -> None:
        self.brain = brain or Brain()
        self.registry = registry or AgentRegistry()
        self.prompt_name = prompt_name
        self._lock = asyncio.Lock()
        # User-consent token-saving mode, owned HERE (not in cfg, not in pipeline).
        # False (default) = every decision via Mind. True = explicit single
        # agent-name mention is matched, decided AND executed right here in
        # decide_and_run() with 0 LLM calls.
        self.instant_route = bool(instant_route)

    @property
    def instant_route_enabled(self) -> bool:
        """Whether the 0-LLM match-decide-execute path is active."""
        return bool(self.instant_route)

    # -- instant-route (0 LLM calls, matched + decided + executed HERE) -------

    @staticmethod
    def _name_variants(name: str) -> List[str]:
        """Registered name -> matchable spellings: 'Apollo',
        'codingagent', 'coding agent'."""
        n = (name or "").strip()
        if not n:
            return []
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", n)
        spaced = re.sub(r"[-_]+", " ", spaced).strip()
        variants = {n.lower(), n.replace("_", "").replace("-", "").lower()}
        if spaced:
            variants.add(spaced.lower())
        return sorted(v for v in variants if v)

    def _try_instant_route(
        self, user_request: str, available_agents: Optional[List[str]] = None
    ) -> Optional[DecisionResult]:
        """Explicit-name direct route. Returns DecisionResult on hit, else None.

        Conservative by design: only fires when exactly ONE registered agent
        name (any spelling variant) appears in the user message. Zero or 2+
        mentions -> None (caller falls through to the LLM).
        """
        text = (user_request or "").lower()
        if not text.strip():
            return None
        if available_agents is not None:
            names = [str(a).strip() for a in available_agents if str(a).strip()]
        else:
            try:
                names = self.registry.available_agents_names()
            except Exception:
                names = []
        if not names:
            return None
        hits: List[str] = []
        for name in names:
            for variant in self._name_variants(name):
                # word-boundary match so "test" doesn't fire "Pulsar"
                if re.search(r"\b" + re.escape(variant) + r"\b", text):
                    hits.append(name)
                    break
        # dedupe, preserve order
        seen = set()
        unique = [h for h in hits if not (h.lower() in seen or seen.add(h.lower()))]
        if len(unique) != 1:
            return None
        matched = unique[0]
        return DecisionResult(
            action="USE_AGENT",
            agent_name=matched,
            reason=(
                f"Instant-route: user explicitly named '{matched}' "
                "(0 LLM calls; user-consented token-saving mode)"
            ),
            confidence=0.9,
            parameters={"instant_route": True, "matched_agent": matched},
        )

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
        Analyze request and decide what should happen. Pure LLM judgement via
        NebulonMind — no shortcuts here. (The instant-route shortcut lives in
        decide_and_run(), which matches AND executes inside this agent.)

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
        # Use lock to avoid requests.Session thread-safety race when sharing one Brain.
        # Transient failures (timeouts/hangs/5xx) retry with backoff so a slow
        # Mind self-recovers instead of forcing an ASK_USER fallback turn.
        async def _chat_once():
            async with self._lock:
                return await asyncio.to_thread(self.brain.chat, prompt, session_id=session_id)

        try:
            from nak.brain.retry import acall_with_retry, probe_mind

            raw = await acall_with_retry(_chat_once, probe=lambda: probe_mind(self.brain))
        except BrainError as exc:
            logger.warning("Polaris brain.chat BrainError: %s (status=%s)", exc, exc.status)
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
            logger.warning("Polaris brain.chat failed: %s", exc)
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
            logger.warning("Polaris parse failed: %s; raw answer: %s", exc, answer[:600])
            if strict:
                raise
            return DecisionResult(
                action="ASK_USER",
                agent_name=None,
                reason=f"Could not parse decision: {exc}. Please clarify your request.",
                confidence=0.0,
                parameters={"raw_answer": answer[:1000]},
            )

    # -- instant match + decide + execute (all inside THIS agent) ---------------

    async def decide_and_run(
        self,
        user_request: str,
        *,
        task: Optional[str] = None,
        session_id: Optional[str] = None,
        state: Any = None,
        available_agents: Optional[List[str]] = None,
        strict: bool = False,
        instant_route: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Match, decide AND execute inside Polaris.

        When instant_route is on (ctor default or this per-turn flag) and the
        user message explicitly names exactly one registered agent, that agent
        is run right here via AgentExecutor — 0 LLM calls — and the turn
        result comes back executed. The pipeline must NOT re-route or
        re-execute that path; it only observes/verifies the result.

        Anything else (mode off, no/ambiguous name, unresolvable agent) falls
        through to decide() and comes back unexecuted for the pipeline's
        normal ASK/CREATE/USE flow.

        Returns {"decision": DecisionResult, "answer": Optional[str],
                 "run_on": Optional[str], "executed": bool, "steps": list}.
        """
        if not user_request or not user_request.strip():
            raise ValueError("user_request must be non-empty")
        user_request = user_request.strip()

        use_instant = self.instant_route if instant_route is None else bool(instant_route)
        if use_instant:
            try:
                hit = self._try_instant_route(user_request, available_agents)
            except Exception as exc:
                logger.debug("instant-route check failed: %s", exc)
                hit = None
            if hit is not None:
                name = hit.agent_name or ""
                from nak.harness.executor import AgentExecutor  # lazy: avoid import cycle

                executor = AgentExecutor(self.brain)
                if executor.is_resolvable(name):
                    try:
                        exec_res = await executor.run_text(
                            name, (task or user_request),
                            session_id=session_id, state=state,
                        )
                    except BrainError as exc:
                        return {
                            "decision": hit,
                            "answer": f"Agent {name} failed: Mind unavailable ({exc}).",
                            "run_on": name,
                            "executed": True,
                            "steps": [],
                        }
                    except Exception as exc:  # noqa: BLE001
                        return {
                            "decision": hit,
                            "answer": f"Agent {name} failed: {exc}.",
                            "run_on": name,
                            "executed": True,
                            "steps": [],
                        }
                    return {
                        "decision": hit,
                        "answer": str(exec_res.get("answer", "")),
                        "run_on": str(exec_res.get("run_on") or name),
                        "executed": True,
                        "steps": exec_res.get("steps", []) or [],
                    }
                # Named but not installed -> unexecuted; pipeline proposes creation.
                return {"decision": hit, "answer": None, "run_on": None,
                        "executed": False, "steps": []}

        result = await self.decide(user_request,
                                   available_agents=available_agents,
                                   strict=strict, session_id=session_id)
        return {"decision": result, "answer": None, "run_on": None,
                "executed": False, "steps": []}

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
