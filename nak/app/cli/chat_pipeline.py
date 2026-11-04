"""
pipeline.chatagent.pipeline -- thin CLI wrapper over HarnessRuntime (Phase 1).

Historically this file OWNED the decide->route->run lifecycle. The harness
runtime (nak.harness.HarnessRuntime) now owns it. This class keeps the exact
public contract (handle/create_confirmed/run_agent_text/...) so the terminal
and tests keep working unchanged, while every turn flows through
HarnessRuntime with explicit HarnessState + workspace isolation.

Boundary tools live at the bottom of THIS file (not in __main__.py):
- PIPELINE_TOOL_SPEC(S) + execute_tool_call(): the pipeline as a callable
  tool — same shape as AGENT_TOOLS, so builders / delegate / the future
  canvas can drop the whole pipeline in as one node.
- build_parser() + main(): the terminal boundary — single-shot CLI, one
  service call per invocation, no REPL. `__main__.py` is only the entry
  shim (`python -m pipeline.chatagent`) that calls main().
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from nak.agents.Genesis.agent import Genesis
from nak.agents.Polaris.agent import Polaris
from nak.brain.client import Brain  # noqa: F401
from nak.harness import HarnessRuntime
from nak.utils.agent_registry import AgentRegistry

from nak.agents.Polaris.planning import make_plan, needs_planning, planning_mode


class ChatAgentPipeline:
    """Production pipeline: [plan] -> decide -> route -> run-or-ask.

    The optional pre-execution plan step lives HERE (terminal layer only):
    when enabled, a Kepler draft is seeded into the turn state's plan,
    which HarnessRuntime then feeds to the worker as context. The runtime
    itself is untouched.
    """

    def __init__(
        self,
        brain: Optional[Brain] = None,
        registry: Optional[AgentRegistry] = None,
        decision_agent: Optional[Polaris] = None,
        creator: Optional[Genesis] = None,
        session_id: Optional[str] = None,
        auto_session: bool = True,
        planning: Optional[str] = None,
        instant_route: bool = False,
    ) -> None:
        self.runtime = HarnessRuntime(
            brain=brain,
            registry=registry,
            decision_agent=decision_agent,
            creator=creator,
            auto_session=False,  # session wiring below preserves legacy behaviour
            instant_route=instant_route,  # user-consent token-saving mode -> Polaris
        )
        # legacy: explicit session_id wins; else auto-create like before
        if session_id is not None:
            self.runtime.session_id = session_id
        elif auto_session:
            self.runtime.session_id = self.runtime._open_session()
        # planning gate (terminal-layer only; see planning.py)
        self.planning = planning_mode(planning)
        # last pre-execution plan (for terminal display/tests; [] = none)
        self.last_plan: List[str] = []
        # back-compat attribute mirrors (terminal reads these directly)
        self._history: List[Dict[str, str]] = self.runtime._history

    # -- attribute mirrors -------------------------------------------------

    @property
    def brain(self):  # type: ignore
        return self.runtime.brain

    @property
    def registry(self):  # type: ignore
        return self.runtime.registry

    @property
    def decision(self):  # type: ignore
        return self.runtime.decision

    @property
    def creator(self):  # type: ignore
        return self.runtime.creator

    @property
    def session_id(self) -> Optional[str]:
        return self.runtime.session_id

    @session_id.setter
    def session_id(self, value: Optional[str]) -> None:
        self.runtime.session_id = value

    # -- delegated API (contract unchanged) --------------------------------

    def is_agent_resolvable(self, agent_name: str) -> bool:
        return self.runtime.is_agent_resolvable(agent_name)

    async def run_agent_text(
        self, agent_name: str, text: str, **kwargs: Any
    ) -> Dict[str, Any]:
        return await self.runtime.run_agent_text(agent_name, text, **kwargs)

    async def handle(self, text: str, *, auto_create: bool = False,
                     instant_route: Optional[bool] = None) -> Dict[str, Any]:
        # Terminal-layer planning gate: pre-create the turn state so the plan
        # step shares the turn's workate.plan, then delegate.
        # Mode "never" (or a simple request) takes the legacy single call.
        # instant_route=None follows the pipeline/Polaris mode; True/False
        # overrides it for this turn (user consent passed from the terminal).
        self.last_plan = []
        if text and text.strip() and needs_planning(text, self.planning):
            st = self.runtime.create_state(text)
            steps = await make_plan(self.runtime, text, st)
            if steps:
                st.plan = list(steps)
                self.last_plan = list(steps)
            return await self.runtime.handle(text, auto_create=auto_create, state=st,
                                             instant_route=instant_route)
        return await self.runtime.handle(text, auto_create=auto_create, instant_route=instant_route)

    async def create_confirmed(self, decision_dict: Dict[str, Any]) -> Optional[Path]:
        return await self.runtime.create_confirmed(decision_dict)

    def list_agents_str(self) -> str:
        return self.runtime.list_agents_str()

    def reset_session(self) -> Optional[str]:
        return self.runtime.reset_session()


# -- API boundary: the pipeline as callable tool(s) --------------------------
# Same {type:function} shape as nak.plugins.tool.agent_tools.AGENT_TOOLS so
# the drag-drop canvas and delegate() can treat the whole pipeline as one
# node ("ask the harness"). Stable names: "pipeline_ask", etc.

PIPELINE_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "pipeline_ask",
        "description": "One full NebulonAK turn: decide -> route -> run -> verify. "
                       "Returns {decision, answer, run_on, needs_confirm, workspace}.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "User message"},
                "auto_create": {"type": "boolean", "description": "Auto-create proposed agent"},
                "instant_route": {"type": "boolean",
                              "description": "User-consented token-saving mode: explicit "
                                             "agent-name mention routes with 0 LLM calls"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}

PIPELINE_TOOL_SPECS = [PIPELINE_TOOL_SPEC]


def execute_tool_call(pipe: "ChatAgentPipeline", name: str, arguments: Any) -> str:
    """Chatagent machine boundary: whole pipeline as one tool call.

    Thin delegate — the generic runner lives in
    nak.plugins.tool.terminal_tool so other pipelines reuse it.
    Loop-safe JSON errors (never raises)."""
    import asyncio as _asyncio

    from nak.plugins.tool import terminal_tool as _terminal

    def _run_turn(text: str, *, auto_create: bool = False,
                  instant_route=None) -> Dict[str, Any]:
        return _asyncio.run(pipe.handle(text, auto_create=auto_create,
                                        instant_route=instant_route))

    return _terminal.execute_tool_call(_run_turn, name, arguments)


# -- terminal boundary: single-shot CLI (no REPL) ------------------------------
# The terminal owns interactivity (bash read-loop, canvas button); this only
# maps argv -> one NakService call -> stdout. No input(), no while-True here.
# Grammar comes from the shared nak.plugins.tool.terminal_tool so every
# pipeline reuses the same file; this pipeline includes all four groups.

def build_parser():  # type: ignore
    """Chatagent grammar = shared terminal grammar, all groups included."""
    from nak.plugins.tool import terminal_tool

    return terminal_tool.build_parser(prog="pipeline.chatagent")


def main(argv=None) -> int:
    """Chatagent terminal entry. Thin delegate — the generic argv -> service
    -> stdout runner lives in nak.plugins.tool.terminal_tool.main so other
    pipelines reuse it. Returns the process exit code."""
    from nak.plugins.tool import terminal_tool as _terminal
    from nak.service import create_service as _create_service

    return _terminal.main(argv, prog="pipeline.chatagent",
                          include=_terminal.ALL_GROUPS,
                          create_service=_create_service)


__all__ = ["ChatAgentPipeline", "PIPELINE_TOOL_SPEC", "PIPELINE_TOOL_SPECS",
           "execute_tool_call", "build_parser", "main"]
