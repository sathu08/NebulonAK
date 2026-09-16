"""nak.service.chat -- headless service over ChatAgentPipeline/HarnessRuntime.

No terminal, no chat loop, no prompts. The terminal CLI
(pipeline/chatagent/__main__.py) and the future builders call into here.

All methods are request -> JSON-serialisable dict. Async first (the
runtime is async); *_sync twins exist for CLI / generated code.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from nak.brain.client import Brain, BrainError
from nak.utils.agent_registry import AgentRegistry

# Stable op ids — drag-drop node types + generated-code function names.
OP_CHAT_TURN = "nak.chat_turn"
OP_LIST_AGENTS = "nak.list_agents"
OP_CREATE_AGENT = "nak.create_agent"
OP_SUGGEST_NAME = "nak.suggest_name"
OP_HEALTH = "nak.health"
OP_REMEMBER = "nak.remember"
OP_SEARCH = "nak.search"
OP_SESSION_NEW = "nak.session_new"


class NakService:
    """Headless facade. Owns one ChatAgentPipeline; exposes one turn at a time."""

    def __init__(
        self,
        brain: Optional[Brain] = None,
        registry: Optional[AgentRegistry] = None,
        session_id: Optional[str] = None,
        auto_session: bool = True,
        planning: Optional[str] = None,
        cfg_path: Optional[str | Path] = None,
        base_url: Optional[str] = None,
        user: Optional[str] = None,
        instant_route: bool = False,
    ) -> None:
        # Lazy import: keeps `nak.service` importable without pulling the
        # whole pipeline graph for catalog-only users.
        from pipeline.chatagent.pipeline import ChatAgentPipeline

        brain = brain or Brain(base_url=base_url, user=user, cfg_path=cfg_path)
        self.pipe = ChatAgentPipeline(
            brain=brain,
            registry=registry,
            session_id=session_id,
            auto_session=auto_session,
            planning=planning,
            instant_route=instant_route,  # user-consent token-saving mode -> Polaris
        )

    # -- identity ------------------------------------------------------
    @property
    def session_id(self) -> Optional[str]:
        return self.pipe.session_id

    @property
    def planning(self) -> str:
        return self.pipe.planning

    # -- core turn -----------------------------------------------------
    async def chat_turn(self, text: str, *, auto_create: bool = False,
                        instant_route: Optional[bool] = None) -> Dict[str, Any]:
        """One harness turn. Returns the runtime result dict unchanged
        (decision/answer/run_on/needs_confirm/created_path/steps/state/
        verification/workspace/session_id).
        instant_route=None follows the service/Polaris mode; True/False
        overrides it for this turn (user consent passed from the terminal)."""
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        return await self.pipe.handle(text.strip(), auto_create=auto_create,
                                      instant_route=instant_route)

    def chat_turn_sync(self, text: str, *, auto_create: bool = False,
                       instant_route: Optional[bool] = None) -> Dict[str, Any]:
        return asyncio.run(self.chat_turn(text, auto_create=auto_create,
                                          instant_route=instant_route))

    # -- registry / agents ---------------------------------------------
    def list_agents(self) -> Dict[str, Any]:
        names = [m.name for m in self.pipe.registry.list_agents()]
        return {"agents": names, "text": self.pipe.list_agents_str()}

    async def create_agent(
        self,
        name: str,
        description: str,
        capabilities: Optional[List[str]] = None,
        instructions: str = "",
    ) -> Dict[str, Any]:
        """Non-interactive creation. Caller supplies everything (the old
        terminal prompts are gone); name is validated, existing names refuse
        with FileExistsError. Returns {name, path}."""
        from nak.agents.Polaris.models import DecisionResult
        from nak.utils.agent_registry import validate_agent_name

        clean_name = validate_agent_name(name)
        if self.pipe.registry.exists(clean_name):
            raise FileExistsError(f"{clean_name} already exists")
        caps = [c.strip() for c in (capabilities or []) if c.strip()] or ["general"]
        spec = DecisionResult(
            action="CREATE_AGENT",
            agent_name=clean_name,
            reason=(description or "")[:300],
            confidence=1.0,
            parameters={"capabilities": caps},
        )

        async def _yes(_s) -> bool:
            return True  # non-interactive: caller already decided via API params

        path = await self.pipe.creator.create(
            spec,
            _yes,
            description=(description or "")[:300],
            capabilities=caps,
            template_vars={"custom_instructions": (instructions or "").strip()},
        )
        return {"name": clean_name, "path": str(path)}

    def create_agent_sync(self, *a, **k) -> Dict[str, Any]:
        return asyncio.run(self.create_agent(*a, **k))

    # -- pure offline helpers (old REPL fallbacks, now builder defaults) --
    def suggest_name(self, description: str) -> Dict[str, Any]:
        from .suggest import suggest_agent_name

        return {"agent_name": suggest_agent_name(description or "")}

    def capability_options(self, description: str) -> Dict[str, Any]:
        from .suggest import capability_options

        opts = capability_options(description or "")
        return {"options": [{"label": lab, "capabilities": caps} for lab, caps in opts]}

    # -- brain passthrough (no prompts, just calls) ----------------------
    async def health(self) -> Dict[str, Any]:
        brain = self.pipe.brain
        live = await asyncio.to_thread(brain.health_live)
        llm = await asyncio.to_thread(brain.llm_status)
        me = await asyncio.to_thread(brain.ensure_user)
        return {"live": live, "llm": llm, "user": me}

    async def remember(self, text: str) -> Dict[str, Any]:
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        res = await asyncio.to_thread(self.pipe.brain.remember, text.strip())
        return {"result": res}

    async def search(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        if not query or not query.strip():
            raise ValueError("query must be non-empty")
        hits = await asyncio.to_thread(self.pipe.brain.search, query.strip())
        return {"hits": hits[:top_k]}

    def session_new(self) -> Dict[str, Any]:
        sid = self.pipe.reset_session()
        return {"session_id": sid}


def create_service(**kwargs) -> NakService:
    """Factory for generated code / CLI (mirrors NakService ctor)."""
    return NakService(**kwargs)


__all__ = [
    "NakService",
    "create_service",
    "OP_CHAT_TURN",
    "OP_LIST_AGENTS",
    "OP_CREATE_AGENT",
    "OP_SUGGEST_NAME",
    "OP_HEALTH",
    "OP_REMEMBER",
    "OP_SEARCH",
    "OP_SESSION_NEW",
]
