"""nak.harness.tool_registry -- one tool list + one call (Phase 2).

Thin object wrapper over nak.plugins so the harness (and future verifier /
recovery stages) does not care how each tool is implemented:

    registry = ToolRegistry(brain, workspace_root=state.workspace)
    result_json = registry.execute(state, "shell", {"command": "pytest -q"})
    specs = registry.specs()  # LLM-visible OpenAI-compatible tool list
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from nak.plugins import HARNESS_TOOLS, execute_plugin


class ToolRegistry:
    """Harness-facing tool registry (specs + dispatch)."""

    def __init__(
        self,
        brain,
        workspace_root: Optional[str | Path] = None,
    ) -> None:
        self.brain = brain
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root
            else None
        )
        try:
            from nak.plugins.tool.manifest import manifest_specs as _ms

            self._specs = list(_ms() or HARNESS_TOOLS)
        except Exception:
            self._specs = list(HARNESS_TOOLS)

    def specs(self) -> List[Dict[str, Any]]:
        """LLM-visible tool specs — read from tools.json (auto-refreshed)."""
        try:
            from nak.plugins.tool.manifest import manifest_specs

            fresh = manifest_specs()
            if fresh:
                self._specs = list(fresh)
        except Exception:
            pass
        return list(self._specs)

    def names(self) -> List[str]:
        try:
            return [s["function"]["name"] for s in self.specs()]
        except Exception:
            return [s["function"]["name"] for s in self._specs]

    def execute(
        self,
        state,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Policy-gated, workspace-scoped, state-recording dispatch.

        Delegates approval/recording to ToolExecutor so behaviour matches
        agent-loop execution exactly.
        """
        from .executor import ToolExecutor

        runner = ToolExecutor(self.brain, workspace_root=self.workspace_root)
        # prefer the state's workspace when set (per-task isolation)
        ws = getattr(state, "workspace", None) if state is not None else None
        if ws:
            runner.workspace_root = Path(ws).expanduser().resolve()
        return runner.execute(state, tool_name, args or {})


__all__ = ["ToolRegistry"]
